"""An answer that says she did something no action of hers did gets a caveat.

In chat it is kept beside the answer and sent with it; on a call it is said after the
reply and shown with it. On both it follows her reply in the history the model reads
next, as the user got it (bug 37): a "yes, do it" then meets a history that says it
wasn't done.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.chat import context, lifecycle
from app.services.chat.session import ChatSession
from app.services.chat.store import ChatStore, StoredMessage, _json_list
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.tools import ToolOutcome, claims
from app.services.voice.realtime import workers
from app.services.voice.realtime.voice_events import VoiceEvent
from tests.fakes import (
    FakeWS,
    ScriptedLLM,
    build_fake_chat_store,
    build_fake_kernel_runtime,
    build_fake_memory_runtime,
    build_fake_persona,
    build_fake_project_store,
)
from tests.test_voice_confirm import _session as call_session
from tests.test_voice_confirm import _spoken

CLAIM = "I've forgotten that you go to the gym on Tuesdays."
_STAMP = datetime(2026, 9, 27, tzinfo=UTC)
CAVEAT = "My memory didn't actually change just now. Ask me again if you want it to."


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


async def _chat(tmp_path: Path, script: list[dict[str, Any]]) -> tuple[Any, ChatSession, FakeWS]:
    bus = InProcessEventBus()
    memory = await build_fake_memory_runtime(tmp_path / "db", bus)
    kernel = await build_fake_kernel_runtime(memory, bus, policy={"memory.forget": "auto_allow"})
    ws = FakeWS()
    session = ChatSession(
        ws,
        memory,
        ScriptedLLM(script),  # type: ignore[arg-type]
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(memory.store),
        await build_fake_project_store(memory.store),
    )
    return memory, session, ws


def _reply(ws: FakeWS) -> dict[str, Any]:
    [frame] = [f for f in map(json.loads, ws.sent_text) if f["type"] == "reply"]
    return dict(frame)


async def test_a_claim_no_action_backs_gets_a_caveat_kept_beside_the_answer(
    tmp_path: Path,
) -> None:
    """Sent with the answer and stored beside it, never inside it; her history has it
    after the reply, as the user saw it (mutation: drop the check from the turn)."""
    memory, session, ws = await _chat(tmp_path, [{"tokens": [CLAIM]}])

    await session._turn("Forget that I go to the gym.")

    assert _reply(ws) == {
        "type": "reply",
        "text": CLAIM,
        "caveats": [{"family": "memory", "text": CAVEAT}],
    }
    assert session._history[-1] == {"role": "assistant", "content": f"{CLAIM}\n\n{CAVEAT}"}
    assert session._conversation_id is not None
    stored = await session._store.messages(session._conversation_id)
    assert stored[-1].content == CLAIM and stored[-1].caveats == [
        {"family": "memory", "text": CAVEAT}
    ]


async def test_a_claim_its_action_backs_gets_none(tmp_path: Path) -> None:
    memory, session, ws = await _chat(
        tmp_path,
        [_call("memory_forget", query="User goes to the gym on Tuesdays"), {"tokens": [CLAIM]}],
    )
    await memory.service.add(
        MemoryCreate(
            kind=MemoryKind.fact, content="User goes to the gym on Tuesdays.", source="user"
        )
    )

    await session._turn("Forget that I go to the gym.")

    assert _reply(ws)["caveats"] == []


async def test_a_call_says_the_caveat_after_the_reply_and_keeps_it_after_it_in_history(
    tmp_path: Path,
) -> None:
    """(mutations: say it through the chunker, which makes it her words on screen; keep
    it out of history)"""
    memory, _kernel, session = await call_session(tmp_path, [{"tokens": [CLAIM]}])
    screen: list[str] = []
    noted: list[str] = []

    async def said(event: Event) -> None:
        screen.append(event.payload["text"])

    async def caveat(event: Event) -> None:
        noted.append(event.payload["text"])

    session._bus.subscribe(VoiceEvent.assistant_token, said)
    session._bus.subscribe(VoiceEvent.assistant_caveat, caveat)

    await workers.respond(session, "forget that I go to the gym")

    heard = _spoken(session)
    assert heard.index(CLAIM) < heard.index(CAVEAT)
    # Shown apart from her words, as chat shows it under the answer.
    assert "".join(screen) == CLAIM and noted == [CAVEAT]
    assert session.history[-1] == {"role": "assistant", "content": f"{CLAIM}\n\n{CAVEAT}"}
    await memory.store.close()


def test_the_check_never_raises_and_records_only_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes: list[tuple[str, dict[str, Any]]] = []

    class _Box:
        def record(self, level: str, event: str, **fields: Any) -> None:
            notes.append((event, fields))

    monkeypatch.setattr(claims, "current_blackbox", lambda: _Box())
    ran = [ToolOutcome(text="x", ok=False, code="not_found", capability="memory.forget")]

    assert claims.check(CLAIM, ran, (), surface="chat") == ["memory"]  # a failed call backs nothing
    assert notes == [("claims.unsupported", {"families": ["memory"], "surface": "chat"})]

    def broken(*_args: Any, **_kwargs: Any) -> list[str]:
        raise RuntimeError("a bad pattern")

    monkeypatch.setattr(claims, "unsupported", broken)
    assert claims.check(CLAIM, [], (), surface="voice") == []


async def test_a_conversation_from_before_caveats_opens_and_takes_them(tmp_path: Path) -> None:
    """Schema 2 adds the column to a file that has the table without it (mutation: drop
    `ensure_column`)."""
    memory = await build_fake_memory_runtime(tmp_path / "db", InProcessEventBus())
    await memory.store.db.executescript(
        "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id "
        "INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', tool_name "
        "TEXT, tool_ok INTEGER, citations_json TEXT NOT NULL DEFAULT '[]', created_at TEXT "
        "NOT NULL); INSERT INTO chat_messages (conversation_id, role, content, created_at) "
        "VALUES (1, 'assistant', 'An old answer.', '2026-09-01T10:00:00+00:00');"
    )
    store = ChatStore(memory.store)

    await store.init_schema()
    await store.add_message(1, "assistant", CLAIM, caveats=[{"family": "memory", "text": CAVEAT}])

    old, new = await store.messages(1)
    assert old.content == "An old answer." and old.caveats == []
    assert new.caveats == [{"family": "memory", "text": CAVEAT}]


@pytest.mark.parametrize(
    "answer",
    [
        "I removed the cache to reduce memory usage.",
        "I've changed the meeting to Friday, just remember to update the invite.",
        "I've updated the recipe to use 1/2 cup of sugar.",
        "I've added the key dates from the document below.",
        "I'll start with the app's core features.",
        "I've already saved that, you told me last week.",
        "Here is a draft:\n\nHi Sam, I've updated the document and saved it to the folder.",
    ],
)
async def test_an_ordinary_answer_gets_no_caveat(tmp_path: Path, answer: str) -> None:
    """Each got one through the real turn before the table was narrowed."""
    memory, session, ws = await _chat(tmp_path, [{"tokens": [answer]}])

    await session._turn("Hello")

    assert _reply(ws)["caveats"] == []
    await memory.store.close()


async def test_a_damaged_citations_value_still_opens(tmp_path: Path) -> None:
    memory = await build_fake_memory_runtime(tmp_path / "db", InProcessEventBus())
    store = await build_fake_chat_store(memory.store)
    await store.add_message(1, "assistant", "Hello.")
    await memory.store.db.execute("UPDATE chat_messages SET citations_json = ?", ('["x"]',))

    [row] = await store.messages(1)

    assert row.content == "Hello." and row.citations == []


async def test_a_damaged_caveats_value_still_opens(tmp_path: Path) -> None:
    """A stored value the model would refuse used to fail the whole conversation."""
    assert _json_list("not json") == [] and _json_list('{"a": 1}') == []
    memory = await build_fake_memory_runtime(tmp_path / "db", InProcessEventBus())
    store = await build_fake_chat_store(memory.store)
    await store.add_message(
        1, "assistant", "Hello.", caveats=[{"family": "memory", "text": CAVEAT}]
    )
    await memory.store.db.execute(
        "UPDATE chat_messages SET caveats_json = ?", ('[{"family": null}, "x", {"text": "Kept."}]',)
    )

    [row] = await store.messages(1)

    assert row.content == "Hello." and row.caveats == [{"text": "Kept."}]


async def test_the_next_turn_reads_the_caveat_after_her_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next turn's prompt carries the answer as the user saw it, the caveat after it,
    and the turn it was written in never saw it (mutation: keep it out)."""
    prompts: list[list[dict[str, Any]]] = []

    async def assembled(*_args: Any, **_kwargs: Any) -> context.TurnContext:
        return context.TurnContext("", "", [])

    monkeypatch.setattr(context, "assemble", assembled)
    memory, session, _ws = await _chat(tmp_path, [{"tokens": [CLAIM]}, {"tokens": ["Sure."]}])
    original = session._llm.stream_chat  # type: ignore[attr-defined]

    def recording(messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        prompts.append([dict(m) for m in messages])
        return original(messages, **kwargs)

    session._llm.stream_chat = recording  # type: ignore[method-assign]

    await session._turn("Forget that I go to the gym.")
    await session._turn("Yes, please do.")

    first, second = prompts
    assert CAVEAT not in str(first)
    assert {"role": "assistant", "content": f"{CLAIM}\n\n{CAVEAT}"} in second


def test_a_reply_is_given_back_with_its_caveats_after_it() -> None:
    """(mutations: in another order; the reply alone)"""
    assert claims.with_caveats("Done.", []) == "Done."
    assert claims.with_caveats("Done.", ["memory", "files"]) == (
        f"Done.\n\n{claims.CAVEAT['memory']} {claims.CAVEAT['files']}"
    )


def test_a_conversation_opened_again_has_its_claims_caveats_after_her_replies() -> None:
    """In the words the user saw then, which a later wording mustn't change; a search's
    note isn't one (mutations: replay the reply alone; every note; today's wording)."""
    missed = {"family": "missed_memory", "text": "I couldn't reach my memory."}
    older = "My memory stayed as it was just now."  # a wording the table might not have now
    stored = [
        StoredMessage(id=1, role="user", content="Forget the gym.", created_at=_STAMP),
        StoredMessage(
            id=2,
            role="assistant",
            content=CLAIM,
            caveats=[missed, {"family": "memory", "text": older}],
            created_at=_STAMP,
        ),
    ]

    history, _ = lifecycle.replayed(stored)

    assert history == [
        {"role": "user", "content": "Forget the gym."},
        {"role": "assistant", "content": f"{CLAIM}\n\n{older}"},
    ]
    assert claims.with_stored("Hi.", [missed, {"text": "no family"}]) == "Hi."


async def test_a_turn_with_a_search_note_and_a_claim_keeps_only_the_claims_in_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The search's note qualifies the answer on screen; only what she said she did goes
    after the reply for the model (mutation: keep every note)."""

    async def missed(*_args: Any, **_kwargs: Any) -> context.TurnContext:
        return context.TurnContext("", "", [], missed=("memory",))

    monkeypatch.setattr(context, "assemble", missed)
    _memory, session, ws = await _chat(tmp_path, [{"tokens": [CLAIM]}])

    await session._turn("Forget that I go to the gym.")

    assert [c["family"] for c in _reply(ws)["caveats"]] == ["missed_memory", "memory"]
    assert session._history[-1] == {"role": "assistant", "content": f"{CLAIM}\n\n{CAVEAT}"}
