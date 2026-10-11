"""MemoryStore + MemoryService: round-trips, KNN, profile, sensitivity, superseding."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.memory.sensitive import SensitiveContentError
from tests.fakes import FakeEmbedder, build_fake_memory_stack


async def test_insert_get_update_archive_roundtrip(tmp_path: Path) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    created = await service.add(MemoryCreate(kind=MemoryKind.fact, content="Rowan bakes sourdough"))
    assert created.id > 0

    fetched = await service.get(created.id)
    assert fetched is not None and fetched.content == "Rowan bakes sourdough"

    updated = await service.update(created.id, content="Rowan finished the sourdough", pinned=True)
    assert updated is not None and updated.pinned and "finished" in updated.content

    await service.delete(created.id)
    archived = await service.get(created.id)
    assert archived is not None and archived.archived  # soft-archived, not gone
    await store.close()


async def test_knn_ranks_nearest_first(tmp_path: Path) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    tech = await service.add(MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter dough"))
    await service.add(MemoryCreate(kind=MemoryKind.fact, content="mom and family dinner"))

    query_vec = await FakeEmbedder().embed_query("sourdough starter")
    ranked = await store.vectors.knn(query_vec, 5)
    assert ranked[0][0].id == tech.id
    await store.close()


async def test_profile_is_deterministic_and_supersedes(tmp_path: Path) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.set_profile("name", "Rowan")
    assert (await service.get_profile())["name"] == "Rowan"

    await service.set_profile("name", "Rowen")  # upsert supersedes in place
    assert await service.get_profile() == {"name": "Rowen"}
    await store.close()


async def test_sensitivity_filter_refuses_credentials(tmp_path: Path) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    with pytest.raises(SensitiveContentError):
        await service.add(MemoryCreate(kind=MemoryKind.fact, content="my password: hunter2"))
    with pytest.raises(SensitiveContentError):
        await service.add(
            MemoryCreate(kind=MemoryKind.fact, content="cam at rtsp://admin:1234@10.0.0.5/h264")
        )
    with pytest.raises(SensitiveContentError):  # natural-language, not just config-style
        await service.add(
            MemoryCreate(kind=MemoryKind.fact, content="remember my wifi password is hunter2")
        )
    assert await service.list() == []  # nothing was stored
    await store.close()


@pytest.mark.parametrize(
    "content",
    [
        # Access codes a home assistant is likely to hear. They matter more here than
        # API keys, and proactive learning stores confident facts without asking.
        "the safe passcode is 3094",
        "Gate keypad code 5820",
        "the alarm pin is 6173",
        # The extractor's own third-person rewording of "my bank PIN is 4821", which a
        # model filed as a memory in every run of the evaluation before this was caught.
        "User has a bank PIN of 4821",
        "User's PIN 4821",
        "wifi passphrase: bluekettlesunrise",
        "the OTP is 604218",
        "the garage door code is 7390",
        # Token shapes. The values are fake and deliberately the wrong length for a
        # real key, so secret scanners don't flag this file.
        "github pat ghp_FAKE0000000000000000fake0000000000",
        "openai key sk-proj-FAKE0000000000000000fake0000000000",
        "card 4111 1111 1111 1111",  # the standard test card; spaced digits
        # Arabic is supported everywhere else, so the guard must cover it too.
        "كلمة السر هي hunter2",
    ],
)
async def test_sensitivity_filter_covers_home_secrets(tmp_path: Path, content: str) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    with pytest.raises(SensitiveContentError):
        await service.add(MemoryCreate(kind=MemoryKind.fact, content=content))
    assert await service.list() == []
    await store.close()


@pytest.mark.parametrize(
    "content",
    [
        "User is baking sourdough for the weekend",
        "User prefers short answers",
        "User's cat is called Pixel",
        "the code review is on Tuesday",  # 'code' without a secret value
        "User dropped pin 25 on the hiking map",  # a pin number, not a PIN
        "User follows the office code of conduct",
        "User's error code of 404 keeps coming back",
    ],
)
async def test_sensitivity_filter_does_not_block_ordinary_facts(
    tmp_path: Path, content: str
) -> None:
    # A filter that blocks ordinary memories would be worse than one that misses a secret.
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.add(MemoryCreate(kind=MemoryKind.fact, content=content))
    assert len(await service.list()) == 1
    await store.close()


@pytest.mark.parametrize(
    "content",
    [
        # The spaced-slash separator catches "admin / hunter2"; a bare slash is
        # ordinary prose and must not trigger it.
        "User is refactoring the login/logout flow",
        "User keeps secret/private notes in Obsidian",
        "User prefers the pin/unpin gesture",
    ],
)
async def test_filter_does_not_refuse_prose_containing_a_slash(
    tmp_path: Path, content: str
) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.add(MemoryCreate(kind=MemoryKind.fact, content=content))
    assert len(await service.list()) == 1
    await store.close()


async def test_filter_still_catches_a_spaced_credential_pair(tmp_path: Path) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    with pytest.raises(SensitiveContentError):
        await service.add(
            MemoryCreate(kind=MemoryKind.fact, content="the router password / hunter2")
        )
    await store.close()


async def test_editing_an_archived_memory_does_not_resurrect_it(tmp_path: Path) -> None:
    # A deleted memory must never return to retrieval. Re-embedding an archived row on
    # update would recreate its vector for a row the UI hides (the list skips archived
    # rows), so it could never be deleted again.
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    memory = await service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter dough project")
    )
    await service.delete(memory.id)
    assert (await retriever.retrieve("sourdough starter")).items == []

    await service.update(memory.id, content="Sourdough starter dough project v2")

    assert (await retriever.retrieve("sourdough starter")).items == []  # still gone
    await store.close()


async def test_editing_a_pending_candidate_does_not_make_it_retrievable(tmp_path: Path) -> None:
    # review_only's contract: nothing is retrievable until explicitly approved.
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    candidate = await service.add_candidate(
        MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter dough project")
    )

    await service.update(candidate.id, content="Sourdough starter dough project (edited)")

    assert (await retriever.retrieve("sourdough starter")).items == []
    await store.close()


async def test_init_sweeps_vectors_left_behind_for_dead_rows(tmp_path: Path) -> None:
    # Self-heal on startup: a database left with orphan vectors (e.g. by an interrupted
    # write) must not keep feeding a deleted memory into prompts.
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    memory = await service.add(
        MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter dough project")
    )
    # Simulate the damage: archive the row but leave its vector behind.
    await store.db.execute("UPDATE memories SET archived=1 WHERE id=?", (memory.id,))
    await store.db.commit()
    assert (await retriever.retrieve("sourdough starter")).items == []  # knn backstop holds

    await store.close()
    await store.init()  # reopen -> the sweep runs

    async with store.db.execute("SELECT COUNT(*) AS n FROM memory_vec") as cur:
        row = await cur.fetchone()
    assert row is not None and row["n"] == 0  # the orphan vector is gone
    await store.close()


async def test_supersede_archives_old_and_links(tmp_path: Path) -> None:
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    old = await service.add(
        MemoryCreate(kind=MemoryKind.preference, content="favourite coffee is latte")
    )
    new = await service.supersede(
        old.id, MemoryCreate(kind=MemoryKind.preference, content="favourite coffee is espresso")
    )
    assert new.supersedes_id == old.id

    old_row = await service.get(old.id)
    assert old_row is not None and old_row.archived
    new_row = await service.get(new.id)
    assert new_row is not None and not new_row.archived
    await store.close()


async def test_mutations_emit_memory_updated(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus.subscribe("memory.updated", collect)
    store, service, _ = await build_fake_memory_stack(tmp_path, bus)
    await service.add(MemoryCreate(kind=MemoryKind.fact, content="Sourdough project"))
    assert seen and seen[-1].payload["op"] == "add"
    await store.close()


async def test_a_memory_from_the_earlier_direction_still_reads(tmp_path: Path) -> None:
    """Scopes and sessions are gone from the code, not from older files: a row that has
    them loads as an ordinary memory (mutation: read the scope into the model again)."""
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    async with store.transaction() as db:
        await db.execute(
            "INSERT INTO memories (kind, scope, content, normalized_content, session_id, "
            "created_at, updated_at) VALUES ('fact', 'incident', 'The side gate sticks', "
            "'the side gate sticks', 7, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )

    assert [memory.content for memory in await service.list()] == ["The side gate sticks"]
    await store.close()
