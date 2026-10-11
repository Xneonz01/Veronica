"""A learned fact that only repeats, in other words, what the person asked her to keep
that day isn't filed (bug 39: told "these days I water them in the evening", she saved it
with her tool, and learning filed "User waters their plants in the evening" too)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.event_bus import InProcessEventBus
from app.services.memory import learning as learning_module
from app.services.memory.fact_words import copy_of
from app.services.memory.models import MemoryCreate, MemoryKind, _now
from tests.fakes import FakeLLM, build_fake_memory_runtime
from tests.test_memory_learning import learn


@pytest.mark.parametrize(
    ("learned", "kept", "copy"),
    [
        # As qwen3.5:9b filed them beside what its tool had saved (measured).
        ("User is allergic to penicillin", ["Allergic to penicillin"], True),
        ("User works nights at the hospital", ["Works nights at the hospital"], True),
        (
            "User waters the balcony garden in the evening",
            ["Waters the balcony garden in the evening and has added basil"],
            True,
        ),
        ("User doesn't eat meat", ["Does not eat meat"], True),  # one negation, two spellings
        # About the one the memory names, not the user (mutation: every mark must match).
        ("Morgan is seven years old", ["Daughter Morgan is seven years old"], True),
        # Naming someone adds nothing to the memory that names them (measured twice).
        ("User has a daughter named Morgan", ["Daughter Morgan is seven years old"], True),
        ("User's partner is called Sam", ["Partner Sam is a teacher"], True),
        # New, or turned around, or about someone else: never a copy (mutations: no
        # negation guard; no guard on who it is about; any shared word).
        ("User runs on Saturday mornings", ["Allergic to penicillin"], False),
        ("User eats meat", ["Doesn't eat meat"], False),
        ("User works as a nurse", ["Sister Morgan works as a nurse"], False),
        ("User is a nurse", ["User's sister is a nurse"], False),
        ("User's daughter has started swimming lessons", ["Daughter Morgan is seven"], False),
        ("User is", ["Is allergic to penicillin"], False),  # nothing that tells facts apart
        ("User likes tea", [], False),
    ],
)
def test_a_copy_says_nothing_the_memory_doesnt(learned: str, kept: list[str], copy: bool) -> None:
    assert copy_of(learned, kept) is copy


def _extraction(*contents: str) -> FakeLLM:
    facts = [{"content": content, "confidence": 0.95} for content in contents]
    return FakeLLM([json.dumps({"profile": {}, "facts": facts})])


HEARD = [
    {"role": "user", "content": "Note that I work nights at the hospital."},
    {"role": "assistant", "content": "Saved: you work nights at the hospital."},
    {"role": "user", "content": "Yeah, so I usually sleep until noon."},
]


async def test_what_she_was_asked_to_keep_that_day_isnt_learned_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutations: file every fact; hold facts against every memory, however it came)"""
    events: list[tuple[str, dict[str, object]]] = []

    class _Box:
        def record(self, _level: str, event: str, **fields: object) -> None:
            events.append((event, fields))

    monkeypatch.setattr(learning_module, "current_blackbox", lambda: _Box())
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    saved = MemoryCreate(
        kind=MemoryKind.fact, content="Works nights at the hospital", source="user"
    )
    await mem.service.add(saved)
    # Learned before, not asked for: a reworded copy of it is left to the dedup of old.
    await mem.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Takes the bus to work", source="inferred")
    )

    llm = _extraction(
        "User works nights at the hospital",
        "User usually sleeps until noon",
        "User takes the bus to work",
    )
    await learn(mem.learning, HEARD, llm, "fake-model")

    contents = {memory.content for memory in await mem.service.list()}
    assert "User works nights at the hospital" not in contents
    assert {"User usually sleeps until noon", "User takes the bus to work"} <= contents
    assert ("learning.copy_dropped", {"count": 1}) in events
    await mem.store.close()


async def test_a_copy_of_something_kept_days_ago_still_gets_through(tmp_path: Path) -> None:
    """Held only against the day before it, so a wrong match stays rare (mutation: no
    limit on how far back)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    older = await mem.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Works nights at the hospital", source="user")
    )
    two_days_ago = (_now() - timedelta(days=2)).isoformat()
    await mem.store.db.execute(
        "UPDATE memories SET created_at=? WHERE id=?", (two_days_ago, older.id)
    )
    await mem.store.db.commit()

    await learn(mem.learning, HEARD, _extraction("User works nights at the hospital"), "fake-model")

    assert "User works nights at the hospital" in {m.content for m in await mem.service.list()}
    await mem.store.close()
