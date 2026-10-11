"""A finished call, kept until learning has read it (`memory/call_queue.py`): what a
call's end keeps, and how the learning worker reads it, a call before a conversation,
through a model that is away, a restart and a call longer than one extraction."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.adapters.llm.ollama_errors import OllamaUnreachableError
from app.core.event_bus import InProcessEventBus
from app.core.model_activity import ModelActivity
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.learning_worker import LearningWorker
from app.services.chat.store import ChatStore
from app.services.memory.call_queue import CallQueue
from app.services.memory.models import Extraction
from app.services.memory.runtime import MemoryRuntime
from app.services.memory.store import MemoryStore
from app.services.voice.realtime import session_tasks
from tests.fakes import (
    OPEN_STORES,
    FakeDetector,
    FakeLLM,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    ScriptedLLM,
    build_fake_chat_store,
    build_fake_memory_runtime,
)
from tests.test_learning_queue import _Clock
from tests.test_learning_worker import Answer, _Extractor
from tests.test_realtime_session import make_session

CALL = [
    {"role": "assistant", "content": "Good afternoon, Rowan."},
    {"role": "user", "content": "I've started learning the cello."},
    {"role": "assistant", "content": "That's lovely. Lessons?"},
    {"role": "user", "content": "On Thursday evenings."},
]


async def _runtime(tmp_path: Path) -> tuple[MemoryRuntime, ChatStore]:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    return mem, await build_fake_chat_store(mem.store)


def _worker(
    mem: MemoryRuntime,
    chat: ChatStore,
    extractor: _Extractor,
    *,
    calls: CallQueue | None = None,
    max_chars: int = 10_000,
) -> LearningWorker:
    return LearningWorker(
        LearningQueue(mem.store),
        chat,
        extractor,  # type: ignore[arg-type]
        ScriptedLLM([]),
        ModelActivity(),
        calls_live=lambda: 0,
        model="m",
        quiet_s=0.0,
        max_chars=max_chars,
        calls=calls or mem.calls,
    )


async def _kept(store: MemoryStore) -> list[dict[str, Any]]:
    async with store.db.execute(
        "SELECT learned_through, attempts, last_error FROM call_learning ORDER BY id"
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def test_a_finished_call_is_read_filed_and_then_let_go(tmp_path: Path) -> None:
    """Her words and the user's, in order; once read, the call's words are gone
    (mutation: keep the row after its last part)."""
    mem, chat = await _runtime(tmp_path)
    extractor = _Extractor()

    assert await mem.calls.add(CALL) is True
    assert await _worker(mem, chat, extractor).step() is True

    assert extractor.read == [CALL] and extractor.filed == [CALL]
    assert await _kept(mem.store) == [] and await mem.calls.waiting() == 0
    await mem.store.close()


async def test_only_what_was_said_is_kept(tmp_path: Path) -> None:
    """A tool's row, a turn with no words and anything else a history entry carries
    stay out (mutation: keep the history as it came)."""
    mem, chat = await _runtime(tmp_path)
    extractor = _Extractor()
    history = [
        {"role": "user", "content": "Read my notes.", "ts": 12.5},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "files_read"}}]},
        {"role": "tool", "content": "Rowan's door code is 4417."},
        {"role": "assistant", "content": "  "},
        {"role": "assistant", "content": "They're about the boat."},
    ]

    await mem.calls.add(history)
    await _worker(mem, chat, extractor).step()

    assert extractor.read == [
        [
            {"role": "user", "content": "Read my notes."},
            {"role": "assistant", "content": "They're about the boat."},
        ]
    ]
    await mem.store.close()


async def test_nothing_is_kept_of_a_call_in_which_the_user_said_nothing(tmp_path: Path) -> None:
    mem, _chat = await _runtime(tmp_path)

    assert await mem.calls.add([{"role": "assistant", "content": "Hello?"}]) is False
    assert await mem.calls.waiting() == 0
    await mem.store.close()


async def test_a_call_waits_while_the_model_is_away_and_is_learned_once_it_is_back(
    tmp_path: Path,
) -> None:
    """What the call's end used to lose: the model away as it ended (mutation: let the
    call go when the model can't be reached)."""
    mem, chat = await _runtime(tmp_path)
    await mem.calls.add(CALL)
    away: list[Answer] = [OllamaUnreachableError("the model can't be reached")]

    assert await _worker(mem, chat, _Extractor(away)).step() is False
    assert await _kept(mem.store) == [{"learned_through": 0, "attempts": 0, "last_error": None}]

    back = _Extractor()
    assert await _worker(mem, chat, back).step() is True
    assert back.filed == [CALL] and await _kept(mem.store) == []
    await mem.store.close()


async def test_a_kept_call_outlives_a_restart(tmp_path: Path) -> None:
    mem, chat = await _runtime(tmp_path)
    await mem.calls.add(CALL)
    await mem.store.close()

    again = MemoryStore(tmp_path / "memory.db", 8)
    await again.init()
    OPEN_STORES.append(again)
    calls = CallQueue(again)
    await calls.init_schema()
    extractor = _Extractor()
    worker = LearningWorker(
        LearningQueue(again),
        await build_fake_chat_store(again),
        extractor,  # type: ignore[arg-type]
        ScriptedLLM([]),
        ModelActivity(),
        calls_live=lambda: 0,
        model="m",
        quiet_s=0.0,
        max_chars=10_000,
        calls=calls,
    )

    assert await worker.step() is True
    assert extractor.filed == [CALL] and await calls.waiting() == 0
    await again.close()


async def test_a_call_longer_than_one_extraction_is_read_in_parts(tmp_path: Path) -> None:
    """Each part fits the window, the next starts where it stopped, and the call goes
    after the last (mutations: read it whole; start again from its first turn)."""
    mem, chat = await _runtime(tmp_path)
    await mem.calls.add(CALL)
    extractor = _Extractor()
    worker = _worker(mem, chat, extractor, max_chars=90)  # two turns and their labels

    while await worker.step():
        pass

    assert extractor.read == [CALL[:2], CALL[2:]]
    assert await _kept(mem.store) == []
    await mem.store.close()


async def test_a_call_is_learned_before_a_conversation(tmp_path: Path) -> None:
    """Its words are kept only until they are read (mutation: conversations first)."""
    mem, chat = await _runtime(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    await mem.calls.add(CALL)
    extractor = _Extractor()
    worker = _worker(mem, chat, extractor)

    await worker.step()
    await worker.step()

    assert extractor.read == [CALL, [{"role": "user", "content": "I live in Leeds"}]]
    await mem.store.close()


async def test_a_call_that_fails_waits_and_says_why(tmp_path: Path) -> None:
    """The same backoff as a conversation's (mutation: try again at once)."""
    mem, chat = await _runtime(tmp_path)
    clock = _Clock()
    calls = CallQueue(mem.store, clock=clock)
    await calls.add(CALL)
    worker = _worker(mem, chat, _Extractor([RuntimeError("cut off")]), calls=calls)

    assert await worker.step() is True
    assert await _kept(mem.store) == [
        {"learned_through": 0, "attempts": 1, "last_error": "RuntimeError"}
    ]
    assert await calls.next_due() is None  # backing off
    clock.now += timedelta(seconds=61)
    assert await calls.next_due() is not None
    await mem.store.close()


async def test_a_kept_call_that_cant_be_read_is_let_go(tmp_path: Path) -> None:
    """Only an edit from outside writes one; read, it would stop the worker at every
    try (mutation: read what is kept as it is)."""
    mem, chat = await _runtime(tmp_path)
    await mem.calls.add(CALL)
    async with mem.store.transaction() as db:
        await db.execute("UPDATE call_learning SET turns_json = '[{\"role\": 7}]'")
    extractor = _Extractor()

    assert await _worker(mem, chat, extractor).step() is True

    assert extractor.read == [] and await mem.calls.waiting() == 0
    await mem.store.close()


async def test_the_end_of_a_call_keeps_it_for_learning(tmp_path: Path) -> None:
    """(mutation: learn at the call's end again, or keep nothing)"""
    mem, _chat = await _runtime(tmp_path)
    session = SimpleNamespace(memory=mem)

    await session_tasks.keep_for_learning(session, CALL)  # type: ignore[arg-type]

    assert await mem.calls.waiting() == 1
    await mem.store.close()


async def test_a_call_that_cant_be_kept_never_breaks_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    mem, _chat = await _runtime(tmp_path)

    async def refused(_history: object) -> bool:
        raise OSError("disk full at C:\\Users\\someone\\data")

    monkeypatch.setattr(mem.calls, "add", refused)

    await session_tasks.keep_for_learning(SimpleNamespace(memory=mem), CALL)  # type: ignore[arg-type]

    assert "OSError" in caplog.text and "someone" not in caplog.text
    await mem.store.close()


async def test_an_extraction_that_gives_nothing_still_lets_the_call_go(tmp_path: Path) -> None:
    mem, chat = await _runtime(tmp_path)
    await mem.calls.add(CALL)

    assert await _worker(mem, chat, _Extractor([Extraction()])).step() is True

    assert await mem.calls.waiting() == 0
    await mem.store.close()


async def test_a_call_that_ends_is_kept_for_learning(tmp_path: Path) -> None:
    """Beside its recap, off the hot path (mutation: leave it out of the call's end)."""
    mem, _chat = await _runtime(tmp_path)
    session = make_session(
        FakeWS(),
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM(["we talked about the cello"]),
        sink=ListMetricsSink(),
        memory=mem,
    )
    session.history = list(CALL)

    session._spawn_session_summary()
    for _ in range(2000):
        if await mem.calls.waiting():
            break
        await asyncio.sleep(0.001)

    assert await mem.calls.waiting() == 1
    await mem.store.close()
