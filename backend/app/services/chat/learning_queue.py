"""Which conversations have messages learning hasn't read yet.

Chat used to learn once, when its socket closed. A conversation switched away from was
never read, quitting often cut the extraction off, and a model that failed lost the
session's facts for good. Now every stored message marks its conversation due, in the
transaction that stores it (`ChatStore.add_message`), and one worker
(`learning_worker.py`) reads the due conversations once the model is free.

A marker, never a transcript: a row holds a conversation id, the last message learning
has read, since when newer ones have waited, and how the last attempts went. The words
stay in `chat_messages`, where the worker reads them.

A failed attempt keeps the conversation due and backs off (`memory/learning_retry.py`,
as a finished call's does). `attempts` counts the failures in a row, and `last_error`
says why the last one failed: `unreadable` for an answer that wasn't the JSON asked for,
which the worker answers by reading less next time (`learning_worker.py`), or the type
of what stopped the request, never its words (a message can quote the reply), until one
succeeds. Added in 0.30.2 without a version of the file: an older build leaves it as it
is.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import NamedTuple

import aiosqlite

from app.services.memory.learning_retry import stamp, wait_after
from app.services.memory.models import _now
from app.services.memory.store import MemoryStore

# Part of the chat schema (`ChatStore.init_schema`): the store marks rows as it writes.
SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_learning (
  conversation_id INTEGER PRIMARY KEY,
  learned_through INTEGER NOT NULL DEFAULT 0,
  due_since TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  retry_after TEXT,
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_learning_due ON chat_learning(due_since);
"""


async def mark(db: aiosqlite.Connection, conversation_id: int, message_id: int) -> None:
    """Mark a conversation due, inside the transaction that stored `message_id`.

    A conversation marked for the first time starts after the messages before this one.
    Those were written before this queue existed, when learning ran once at the end of a
    session: it had its chance at them, and reading them again would file twice what it
    did learn. A conversation that is gone (deleted while a reply was still being
    written) isn't marked.
    """
    await db.execute(
        "INSERT INTO chat_learning (conversation_id, learned_through, due_since) "
        "SELECT ?, (SELECT COALESCE(MAX(id), 0) FROM chat_messages "
        "WHERE conversation_id = ? AND id < ?), ? "
        "WHERE EXISTS (SELECT 1 FROM chat_conversations WHERE id = ?) "
        "ON CONFLICT(conversation_id) DO UPDATE SET "
        "due_since = COALESCE(chat_learning.due_since, excluded.due_since)",
        (conversation_id, conversation_id, message_id, stamp(_now()), conversation_id),
    )


async def forget(db: aiosqlite.Connection, conversation_id: int) -> None:
    """Drop a deleted conversation's marker, inside the transaction deleting it."""
    await db.execute("DELETE FROM chat_learning WHERE conversation_id = ?", (conversation_id,))


class Due(NamedTuple):
    """A conversation with messages to read, as the worker took it."""

    conversation_id: int
    learned_through: int
    attempts: int
    last_error: str | None = None


class Backlog(NamedTuple):
    """Conversations waiting to be learned from, and the failures holding some back."""

    due: int
    failing: dict[str, int]  # the last error's type -> conversations


class LearningQueue:
    """The worker's side of the queue: take the next conversation, and say how it went."""

    def __init__(self, memory: MemoryStore, *, clock: Callable[[], datetime] = _now) -> None:
        self._memory = memory
        self._clock = clock

    async def next_due(self) -> Due | None:
        """The conversation that has waited longest and isn't backing off."""
        async with self._memory.db.execute(
            "SELECT conversation_id, learned_through, attempts, last_error FROM chat_learning "
            "WHERE due_since IS NOT NULL AND (retry_after IS NULL OR retry_after <= ?) "
            "ORDER BY due_since, conversation_id LIMIT 1",
            (stamp(self._clock()),),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Due(int(row[0]), int(row[1]), int(row[2]), row[3])

    async def done(self, due: Due, *, through: int) -> None:
        """Learning read up to message `through`.

        The conversation stays due while anything newer waits: a message stored while
        the model was reading, or one past what a single extraction takes.
        """
        async with self._memory.transaction() as db:
            await db.execute(
                "UPDATE chat_learning SET learned_through = MAX(learned_through, ?), "
                "attempts = 0, retry_after = NULL, last_error = NULL, due_since = CASE WHEN EXISTS "
                "(SELECT 1 FROM chat_messages WHERE conversation_id = ? AND id > ?) "
                "THEN due_since ELSE NULL END WHERE conversation_id = ?",
                (through, due.conversation_id, through, due.conversation_id),
            )

    async def failed(self, due: Due, error: str) -> int:
        """Keep it due and wait before the next attempt, saying why this one failed.
        Returns the wait in seconds."""
        wait = wait_after(due.attempts)
        retry = self._clock() + timedelta(seconds=wait)
        async with self._memory.transaction() as db:
            await db.execute(
                "UPDATE chat_learning SET attempts = attempts + 1, retry_after = ?, "
                "last_error = ? WHERE conversation_id = ?",
                (stamp(retry), error, due.conversation_id),
            )
        return wait

    async def backlog(self) -> Backlog:
        """How far behind learning is, for diagnostics: the conversations due, and how
        many of them are waiting after failures, by why the last one failed."""
        async with self._memory.db.execute(
            "SELECT COUNT(*) FROM chat_learning WHERE due_since IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
        # By attempts, not by a reason: a failure from before reasons were kept has none.
        async with self._memory.db.execute(
            "SELECT COALESCE(last_error, 'unknown'), COUNT(*) FROM chat_learning "
            "WHERE due_since IS NOT NULL AND attempts > 0 GROUP BY 1"
        ) as cur:
            failing = {str(error): int(count) for error, count in await cur.fetchall()}
        return Backlog(due=int(row[0]) if row else 0, failing=failing)
