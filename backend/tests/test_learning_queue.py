"""The learning queue: which conversations have messages learning hasn't read yet."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.event_bus import InProcessEventBus
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.store import ChatStore
from app.services.memory.store import MemoryStore
from tests.fakes import build_fake_chat_store, build_fake_memory_runtime


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def _stores(tmp_path: Path) -> tuple[MemoryStore, ChatStore]:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    return mem.store, await build_fake_chat_store(mem.store)


async def _marker(store: MemoryStore, conversation_id: int) -> dict[str, Any] | None:
    async with store.db.execute(
        "SELECT * FROM chat_learning WHERE conversation_id = ?", (conversation_id,)
    ) as cur:
        row = await cur.fetchone()
    return None if row is None else {key: row[key] for key in row.keys()}


async def test_a_stored_message_marks_its_conversation_due_from_the_start(
    tmp_path: Path,
) -> None:
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")

    due = await LearningQueue(store).next_due()

    assert due is not None
    assert (due.conversation_id, due.learned_through, due.attempts) == (conversation.id, 0, 0)
    await store.close()


async def test_a_conversation_from_before_the_queue_starts_after_its_old_messages(
    tmp_path: Path,
) -> None:
    """Learning ran once at the end of their session and had its chance at them;
    reading them again would file twice what it did learn (mutation: start every new
    marker at 0)."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    async with store.transaction() as db:  # stored the way an older build stored them
        for text in ("I live in Leeds", "Noted."):
            await db.execute(
                "INSERT INTO chat_messages (conversation_id, role, content, created_at) "
                "VALUES (?, 'user', ?, '2026-01-01T00:00:00+00:00')",
                (conversation.id, text),
            )
    old = await chat.messages(conversation.id)

    new_id = await chat.add_message(conversation.id, "user", "what is the weather")

    due = await LearningQueue(store).next_due()
    assert due is not None and due.learned_through == old[-1].id
    unread = await chat.messages_after(conversation.id, due.learned_through, limit=10)
    assert [m.id for m in unread] == [new_id]
    await store.close()


async def test_done_clears_a_conversation_learning_has_caught_up_with(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    queue = LearningQueue(store)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")
    last = await chat.add_message(conversation.id, "assistant", "Noted.")

    due = await queue.next_due()
    assert due is not None
    await queue.done(due, through=last)

    assert await queue.next_due() is None
    marker = await _marker(store, conversation.id)
    assert marker is not None
    assert (marker["learned_through"], marker["due_since"]) == (last, None)
    await store.close()


async def test_a_message_stored_while_learning_read_keeps_the_conversation_due(
    tmp_path: Path,
) -> None:
    """Mutation: clear the conversation on `done` whatever came after."""
    store, chat = await _stores(tmp_path)
    queue = LearningQueue(store)
    conversation = await chat.create("t")
    read = await chat.add_message(conversation.id, "user", "I live in Leeds")
    due = await queue.next_due()
    assert due is not None
    await chat.add_message(conversation.id, "user", "and I work nights")  # mid-read

    await queue.done(due, through=read)

    again = await queue.next_due()
    assert again is not None and again.learned_through == read
    await store.close()


async def test_a_failed_attempt_backs_off_a_minute_then_doubling_to_an_hour(
    tmp_path: Path,
) -> None:
    store, chat = await _stores(tmp_path)
    clock = _Clock()
    queue = LearningQueue(store, clock=clock)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")

    waits = []
    for _ in range(8):
        due = await queue.next_due()
        assert due is not None
        wait = await queue.failed(due, "OllamaUnreachableError")
        waits.append(wait)
        clock.now += timedelta(seconds=wait - 1)
        assert await queue.next_due() is None  # backing off
        clock.now += timedelta(seconds=1)

    assert waits == [60, 120, 240, 480, 960, 1920, 3600, 3600]
    due = await queue.next_due()
    assert due is not None and due.attempts == 8
    await queue.done(due, through=due.learned_through + 1)
    marker = await _marker(store, conversation.id)
    assert marker is not None
    assert (marker["attempts"], marker["retry_after"]) == (0, None)  # a success starts over
    await store.close()


async def test_the_longest_waiting_goes_first_and_one_backing_off_holds_up_no_other(
    tmp_path: Path,
) -> None:
    store, chat = await _stores(tmp_path)
    queue = LearningQueue(store)
    newer = await chat.create("a")
    older = await chat.create("b")  # the higher id, but waiting longest
    await chat.add_message(older.id, "user", "one")
    await chat.add_message(newer.id, "user", "two")
    await chat.add_message(older.id, "user", "three")  # still waiting since "one"

    due = await queue.next_due()
    assert due is not None and due.conversation_id == older.id
    await queue.failed(due, "OllamaUnreachableError")

    due = await queue.next_due()
    assert due is not None and due.conversation_id == newer.id
    await store.close()


async def test_a_message_for_a_conversation_that_is_gone_marks_nothing(tmp_path: Path) -> None:
    """A reply still being written when its conversation was deleted is stored with no
    conversation to show it; learning must not read it (mutation: mark regardless)."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.delete(conversation.id)

    await chat.add_message(conversation.id, "assistant", "Noted.")

    assert await _marker(store, conversation.id) is None
    await store.close()


async def test_deleting_a_conversation_drops_its_marker(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "I live in Leeds")

    await chat.delete(conversation.id)

    assert await _marker(store, conversation.id) is None
    assert await LearningQueue(store).next_due() is None
    await store.close()


async def test_the_queue_keeps_a_marker_never_the_words(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "Rowan's locker code is 4417")

    marker = await _marker(store, conversation.id)

    assert marker is not None
    assert set(marker) == {
        "conversation_id",
        "learned_through",
        "due_since",
        "attempts",
        "retry_after",
        "last_error",  # a type's name, never the words
    }
    assert not any("Rowan" in str(value) or "4417" in str(value) for value in marker.values())
    await store.close()


async def test_why_the_last_attempt_failed_is_kept_until_one_succeeds(tmp_path: Path) -> None:
    """Only in the black box before, whose runs rotate: a queue stuck for a week had
    nothing to say why (mutations: don't keep it; keep it after a success)."""
    store, chat = await _stores(tmp_path)
    clock = _Clock()
    queue = LearningQueue(store, clock=clock)
    stuck = await chat.create("stuck")
    await chat.add_message(stuck.id, "user", "I live in Leeds")
    fine = await chat.create("fine")
    await chat.add_message(fine.id, "user", "I work nights")

    due = await queue.next_due()
    assert due is not None
    await queue.failed(due, "OllamaUnreachableError")

    assert (await _marker(store, due.conversation_id) or {})["last_error"] == (
        "OllamaUnreachableError"
    )
    assert await queue.backlog() == (2, {"OllamaUnreachableError": 1})

    clock.now += timedelta(hours=2)
    again = await queue.next_due()
    assert again is not None and again.conversation_id == due.conversation_id
    await queue.done(again, through=10**9)
    assert (await _marker(store, due.conversation_id) or {})["last_error"] is None
    assert (await queue.backlog()).failing == {}
    await store.close()
