"""The learning worker: reads due conversations once the model is free."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaUnreachableError,
)
from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.core.model_activity import ModelActivity
from app.main import create_app
from app.services.chat import learning_worker
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.learning_sources import unread
from app.services.chat.learning_worker import LearningWorker, transcript_budget
from app.services.chat.store import ChatStore, StoredMessage
from app.services.memory.models import Extraction, _now
from app.services.memory.store import MemoryStore
from tests.fakes import ScriptedLLM, build_fake_chat_store, build_fake_memory_runtime
from tests.test_learning_queue import _Clock

Said = list[dict[str, str]]
Answer = Extraction | BaseException | None


class _Extractor:
    """Stands in for `LearningController`: records what it read, with the turns before
    (`earlier`), and filed; answers as told: an extraction, None (unreadable) or an error."""

    def __init__(
        self, answers: Sequence[Answer] = (), during: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        self.read: list[Said] = []
        self.earlier: list[Said] = []
        self.filed: list[Said] = []
        self.shown: list[bool] = []
        self.answers = list(answers)
        self.during = during

    async def extract(
        self,
        history: Sequence[Mapping[str, Any]],
        _llm: object,
        _model: str,
        earlier: Sequence[Mapping[str, Any]] = (),
    ) -> Extraction | None:
        self.read.append(_turns(history))
        self.earlier.append(_turns(earlier))
        if self.during is not None:
            await self.during()
        answer = self.answers.pop(0) if self.answers else Extraction()
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def file(
        self, _: Extraction, history: Sequence[Mapping[str, Any]], earlier: Sequence[Any] = ()
    ) -> None:
        self.filed.append(_turns(history))
        self.shown.append(bool(earlier))


def _turns(history: Sequence[Mapping[str, Any]]) -> Said:
    return [{"role": m["role"], "content": m["content"]} for m in history]


async def _stores(tmp_path: Path) -> tuple[MemoryStore, ChatStore]:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    return mem.store, await build_fake_chat_store(mem.store)


def _worker(
    store: MemoryStore,
    chat: ChatStore,
    extractor: _Extractor,
    *,
    queue: LearningQueue | None = None,
    max_chars: int = 10_000,
    calls_live: Callable[[], int] = lambda: 0,
    activity: ModelActivity | None = None,
    quiet_s: float = 0.0,
) -> LearningWorker:
    return LearningWorker(
        queue or LearningQueue(store),
        chat,
        extractor,  # type: ignore[arg-type]
        ScriptedLLM([]),
        activity or ModelActivity(),
        calls_live=calls_live,
        model="m",
        quiet_s=quiet_s,
        max_chars=max_chars,
    )


async def _attempts(store: MemoryStore, conversation_id: int) -> int:
    async with store.db.execute(
        "SELECT attempts FROM chat_learning WHERE conversation_id = ?", (conversation_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


class _Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, _level: str, event: str, **_fields: object) -> None:
        self.events.append(event)


async def test_a_due_conversation_is_read_once_filed_and_then_caught_up(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    await chat.add_message(conversation.id, "assistant", "Noted.")
    extractor = _Extractor()
    worker = _worker(store, chat, extractor)

    assert await worker.step() is True
    assert await worker.step() is False  # nothing left

    said = [
        {"role": "user", "content": "I live in Leeds"},
        {"role": "assistant", "content": "Noted."},
    ]
    assert extractor.read == extractor.filed == [said]
    await store.close()


async def test_tool_rows_and_cited_replies_stay_out_of_what_it_reads(tmp_path: Path) -> None:
    """A cited reply repeats a document, and the extractor can't tell that from the user
    talking about themselves (mutation: read every assistant reply)."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "how do I wire the circuit?")
    await chat.add_message(conversation.id, "tool", "Found 1 document", tool_name="rag_search")
    await chat.add_message(
        conversation.id,
        "assistant",
        "Connect the circuit carefully [1].",
        citations=[{"n": 1, "title": "Guide"}],
    )
    await chat.add_message(conversation.id, "user", "I have a cat")
    await chat.add_message(conversation.id, "assistant", "\n ")  # a reply that came back blank
    await chat.add_message(conversation.id, "assistant", "Nice.")
    extractor = _Extractor()

    await _worker(store, chat, extractor).step()

    assert extractor.read == [
        [
            {"role": "user", "content": "how do I wire the circuit?"},
            {"role": "user", "content": "I have a cat"},
            {"role": "assistant", "content": "Nice."},
        ]
    ]
    await store.close()


def _message(message_id: int, text: str) -> StoredMessage:
    return StoredMessage(id=message_id, role="user", content=text, created_at=_now())


def test_the_labels_count_toward_what_one_extraction_reads() -> None:
    """Each turn goes out as "User: ..." on a line of its own (mutation: count the
    words alone)."""
    messages = [_message(1, "a" * 10), _message(2, "b" * 10)]
    turns, through = unread(messages, 40)  # 20 characters of words, 44 with labels

    assert [turn["content"] for turn in turns] == ["a" * 10]
    assert through == 1
    assert unread([], 40) == ([], 0)


async def test_a_conversation_longer_than_one_extraction_is_read_in_parts(
    tmp_path: Path,
) -> None:
    """A prompt past the model's window loses its instruction without an error, so one
    extraction reads at most `max_chars` (mutation: read the whole page)."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    for text in ("a" * 10, "b" * 10, "c" * 10):
        await chat.add_message(conversation.id, "user", text)
    extractor = _Extractor()
    worker = _worker(store, chat, extractor, max_chars=50)

    assert await worker.step() is True
    assert await worker.step() is True
    assert await worker.step() is False

    parts = [[m["content"] for m in part] for part in extractor.read]
    assert parts == [["a" * 10, "b" * 10], ["c" * 10]]
    await store.close()


async def test_a_first_message_longer_than_one_extraction_is_cut_to_it(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "x" * 50)
    extractor = _Extractor()
    worker = _worker(store, chat, extractor, max_chars=30)

    await worker.step()

    assert extractor.read == [[{"role": "user", "content": "x" * 18}]]  # 30 less its label
    assert await worker.step() is False  # read past, not stuck on it
    await store.close()


async def test_a_conversation_due_with_nothing_after_its_marker_is_cleared(
    tmp_path: Path,
) -> None:
    """Nothing to read is not a reason to stay due: it would be taken again forever
    (mutation: mark it done through 0)."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    last = await chat.add_message(conversation.id, "user", "I live in Leeds")
    async with store.transaction() as db:
        await db.execute(
            "UPDATE chat_learning SET learned_through = ? WHERE conversation_id = ?",
            (last, conversation.id),
        )
    extractor = _Extractor()
    worker = _worker(store, chat, extractor)

    assert await worker.step() is True
    assert await worker.step() is False
    assert extractor.read == []
    await store.close()


def test_one_extraction_fits_the_model_window() -> None:
    assert transcript_budget(8192) == (8192 - 1024) * 3
    assert transcript_budget(2048) > 0  # the smallest window the settings allow


@pytest.mark.parametrize(
    "answer", [None, RuntimeError("the model failed partway")], ids=["unreadable", "raised"]
)
async def test_a_failed_extraction_keeps_the_conversation_due_and_backs_off(
    tmp_path: Path, answer: Answer
) -> None:
    store, chat = await _stores(tmp_path)
    queue = LearningQueue(store)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    extractor = _Extractor([answer])
    worker = _worker(store, chat, extractor, queue=queue)

    assert await worker.step() is True

    assert await queue.next_due() is None  # backing off, not dropped
    assert await _attempts(store, conversation.id) == 1
    assert extractor.filed == []
    await store.close()


async def test_an_unreadable_answer_halves_the_next_read_and_the_least_is_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One paste the model can't read must not stop the rest of a conversation from
    being learned (mutations: never pass over; don't halve)."""
    recorder = _Recorder()
    monkeypatch.setattr(learning_worker, "current_blackbox", lambda: recorder)
    store, chat = await _stores(tmp_path)
    clock = _Clock()
    queue = LearningQueue(store, clock=clock)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "a" * 1500)
    await chat.add_message(conversation.id, "user", "b" * 1500)
    extractor = _Extractor([None, None])
    worker = _worker(store, chat, extractor, queue=queue, max_chars=4000)

    await worker.step()  # both read (4,000), unreadable: backs off
    clock.now += timedelta(hours=1)
    await worker.step()  # half (2,000, the least) holds one: unreadable, passed over
    await worker.step()  # the other one, read and filed

    assert [len(part) for part in extractor.read] == [2, 1, 1]
    assert extractor.filed == [[{"role": "user", "content": "b" * 1500}]]
    assert recorder.events == ["learning.failed", "learning.skipped"]
    assert await worker.step() is False
    await store.close()


@pytest.mark.parametrize(
    "away",
    [
        OllamaUnreachableError("down"),
        OllamaModelMissingError("qwen3.5:9b"),
        OllamaModelFailedError("qwen3.5:9b", "llama-server process has terminated"),
    ],
)
async def test_a_model_that_is_away_or_not_installed_costs_no_attempt(
    tmp_path: Path, away: Exception
) -> None:
    """Neither says anything about what was said: read as an unreadable answer, a model
    being pulled saw every conversation skipped within 20 minutes (mutation: catch only
    the unreachable one)."""
    store, chat = await _stores(tmp_path)
    queue = LearningQueue(store)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    worker = _worker(store, chat, _Extractor([away]), queue=queue)

    assert await worker.step() is False  # nothing to do until it is back

    assert await _attempts(store, conversation.id) == 0
    assert await queue.next_due() is not None  # still due, at once
    await store.close()


async def test_an_extraction_gives_way_when_a_reply_needs_the_model(tmp_path: Path) -> None:
    """A reply never waits behind learning (mutation: let the extraction finish)."""
    store, chat = await _stores(tmp_path)
    queue = LearningQueue(store)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    activity = ModelActivity()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def long_extraction() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()

    extractor = _Extractor(during=long_extraction)
    stepping = asyncio.create_task(_worker(store, chat, extractor, activity=activity).step())
    await asyncio.wait_for(started.wait(), 1.0)
    with activity.busy():  # a chat turn starts streaming
        assert await asyncio.wait_for(stepping, 1.0) is True

    assert stopped.is_set()
    assert extractor.filed == []
    assert await _attempts(store, conversation.id) == 0  # giving way is no failure
    assert await queue.next_due() is not None
    await store.close()


async def test_an_extraction_gives_way_when_a_call_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(learning_worker, "_WATCH_S", 0.01)
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    calls = [0]

    async def call_starts() -> None:
        calls[0] = 1
        await asyncio.sleep(30)

    extractor = _Extractor(during=call_starts)
    worker = _worker(store, chat, extractor, calls_live=lambda: calls[0])

    assert await asyncio.wait_for(worker.step(), 1.0) is True
    assert extractor.filed == []
    await store.close()


async def test_a_conversation_deleted_while_the_model_reads_it_is_not_filed(
    tmp_path: Path,
) -> None:
    store, chat = await _stores(tmp_path)
    queue = LearningQueue(store)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I have a cat")

    async def deleted() -> None:
        await chat.delete(conversation.id)

    extractor = _Extractor(during=deleted)
    await _worker(store, chat, extractor, queue=queue).step()

    assert extractor.read and extractor.filed == []
    assert await queue.next_due() is None
    await store.close()


async def test_a_message_stored_during_the_extraction_is_read_next_time(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")

    async def more() -> None:
        await chat.add_message(conversation.id, "user", "and I work nights")

    extractor = _Extractor(during=more)
    worker = _worker(store, chat, extractor)
    await worker.step()
    extractor.during = None
    await worker.step()

    parts = [[m["content"] for m in part] for part in extractor.read]
    assert parts == [["I live in Leeds"], ["and I work nights"]]
    await store.close()


async def test_it_waits_while_a_call_is_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(learning_worker, "_CALL_POLL_S", 0.01)
    store, chat = await _stores(tmp_path)
    calls = [1]
    worker = _worker(store, chat, _Extractor(), calls_live=lambda: calls[0])

    waiting = asyncio.create_task(worker.wait_until_free())
    await asyncio.sleep(0.05)
    assert not waiting.done()
    calls[0] = 0
    await asyncio.wait_for(waiting, 0.5)
    await store.close()


async def test_it_waits_until_the_model_has_been_quiet_long_enough(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    activity = ModelActivity()
    worker = _worker(store, chat, _Extractor(), activity=activity, quiet_s=0.5)
    with activity.busy():
        waiting = asyncio.create_task(worker.wait_until_free())
        await asyncio.sleep(0.2)
        assert not waiting.done()  # a reply is still streaming
    await asyncio.sleep(0.05)
    assert not waiting.done()  # it ended, but only just
    await asyncio.wait_for(waiting, 2.0)
    await store.close()


async def test_the_worker_outlives_a_store_that_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(learning_worker, "_IDLE_POLL_S", 0)
    store, chat = await _stores(tmp_path)
    worker = _worker(store, chat, _Extractor())
    steps: list[int] = []
    enough = asyncio.Event()

    async def step() -> bool:
        steps.append(1)
        if len(steps) == 3:
            enough.set()
        if len(steps) == 1:
            raise RuntimeError("database is locked")
        return False

    worker.step = step  # type: ignore[method-assign]
    running = asyncio.create_task(worker.run())
    await asyncio.wait_for(enough.wait(), 1.0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert len(steps) >= 3
    await store.close()


def test_the_app_runs_the_worker_for_as_long_as_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Started with the app, reading finished calls and then conversations, and stopped
    at quit (mutations: never start it; leave out the calls; leave it running). The
    step is a stand-in, so nothing here reaches a real model."""
    monkeypatch.setenv("VERONICA_MEMORY__LEARNING_QUIET_S", "0")
    get_settings.cache_clear()
    stepped = threading.Event()
    tasks: list[asyncio.Task[Any]] = []

    sources: list[list[str]] = []

    async def step(_self: LearningWorker) -> bool:
        task = asyncio.current_task()
        assert task is not None
        tasks.append(task)
        sources.append([type(source).__name__ for source in _self._sources])
        stepped.set()
        return False

    monkeypatch.setattr(LearningWorker, "step", step)
    try:
        with TestClient(create_app(), base_url="http://localhost"):
            assert stepped.wait(5)
            assert not tasks[0].done()
            assert sources[0] == ["CallSource", "ChatSource"]
    finally:
        get_settings.cache_clear()
    assert tasks[0].done()
