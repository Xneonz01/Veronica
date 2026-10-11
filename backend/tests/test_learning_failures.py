"""Why learning failed, as the queue keeps it and diagnostics show it (0.30.2): the type
of what stopped the request, or `unreadable` for an answer that wasn't the JSON asked
for, which alone reads less next time. Every failure was once `unreadable`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.types import LlmChunk
from app.api import routes_system
from app.core.event_bus import InProcessEventBus
from app.core.model_activity import ModelActivity
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.learning_worker import LearningWorker
from app.services.memory.models import Extraction
from tests.fakes import FakeLLM, build_fake_chat_store, build_fake_memory_runtime
from tests.test_learning_queue import _Clock
from tests.test_learning_worker import _Extractor, _stores, _worker


async def _last_error(store: object, conversation_id: int) -> str | None:
    async with store.db.execute(  # type: ignore[attr-defined]
        "SELECT last_error FROM chat_learning WHERE conversation_id = ?", (conversation_id,)
    ) as cur:
        row = await cur.fetchone()
    return None if row is None else row[0]


async def test_a_model_that_fails_mid_answer_is_kept_by_its_type(tmp_path: Path) -> None:
    """Through the real controller, which took every error for an unreadable answer
    (mutations: catch every error in the controller; record `unreadable` for it)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    chat = await build_fake_chat_store(mem.store)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    worker = LearningWorker(
        LearningQueue(mem.store),
        chat,
        mem.learning,
        FakeLLM(["{"], crash=True),  # type: ignore[arg-type]
        ModelActivity(),
        calls_live=lambda: 0,
        model="m",
        quiet_s=0.0,
        max_chars=10_000,
    )

    assert await worker.step() is True

    assert await _last_error(mem.store, conversation.id) == "RuntimeError"
    await mem.store.close()


async def test_only_an_answer_that_couldnt_be_read_reads_less_next_time(tmp_path: Path) -> None:
    """A timeout says nothing about the length: it halved what was read all the same
    (mutation: halve after any failure)."""
    store, chat = await _stores(tmp_path)
    clock = _Clock()
    queue = LearningQueue(store, clock=clock)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "a" * 1500)
    await chat.add_message(conversation.id, "user", "b" * 1500)
    extractor = _Extractor([TimeoutError("slow"), None, Extraction()])
    worker = _worker(store, chat, extractor, queue=queue, max_chars=4000)

    for _ in range(3):
        await worker.step()
        clock.now += timedelta(hours=2)

    # Whole after the timeout; half (one message) after the answer it couldn't read.
    assert [len(part) for part in extractor.read] == [2, 2, 1]
    await store.close()


async def test_diagnostics_count_the_waiting_and_the_failing(tmp_path: Path) -> None:
    """Values, not only its keys (mutation: a fixed backlog); and a failure from before
    reasons were kept still counts, as `unknown`."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    chat = await build_fake_chat_store(mem.store)
    queue = LearningQueue(mem.store)
    for title in ("failed", "waiting", "failed before 0.30.2"):
        conversation = await chat.create(title)
        await chat.add_message(conversation.id, "user", f"about {title}")
    due = await queue.next_due()  # the first, which has waited longest
    assert due is not None and due.conversation_id == 1
    await queue.failed(due, "RuntimeError")
    async with mem.store.transaction() as db:  # failed with no reason kept
        await db.execute("UPDATE chat_learning SET attempts = 2 WHERE conversation_id = 3")

    learning = await routes_system._learning(mem)

    assert learning == {"due": 3, "failing": {"RuntimeError": 1, "unknown": 1}, "calls": 0}
    await mem.store.close()


async def test_diagnostics_stand_without_a_backlog_they_cant_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole page failed, and About with it (mutation: let the error through)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())

    async def refuse(_self: LearningQueue) -> None:
        raise OSError("disk I/O error")

    monkeypatch.setattr(LearningQueue, "backlog", refuse)

    assert await routes_system._learning(mem) is None
    await mem.store.close()


class _Leaky:
    """A model whose failure quotes what it was given."""

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        *,
        model: str | None = None,
        keep_alive: str | None = None,
        temperature: Any = None,
    ) -> AsyncIterator[LlmChunk]:
        raise ValueError("Rowan's locker code is 4417")
        yield LlmChunk()  # an async generator, as the real client is


async def test_what_a_failed_extraction_says_stays_out_of_the_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An error's message can quote the model's answer, and so the user: only its type is
    logged at a level the black box copies (mutation: log the exception itself)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    chat = await build_fake_chat_store(mem.store)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "hello")
    worker = LearningWorker(
        LearningQueue(mem.store),
        chat,
        mem.learning,
        _Leaky(),  # type: ignore[arg-type]
        ModelActivity(),
        calls_live=lambda: 0,
        model="m",
        quiet_s=0.0,
        max_chars=10_000,
    )

    with caplog.at_level(logging.DEBUG):
        assert await worker.step() is True

    loud = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert loud and await _last_error(mem.store, conversation.id) == "ValueError"
    assert all("4417" not in record.getMessage() and record.exc_info is None for record in loud)
    await mem.store.close()
