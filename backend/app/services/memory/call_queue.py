"""The calls learning hasn't read yet, kept until it has.

A call used to be learned from once, as it ended: when the model couldn't be reached
then, or its answer couldn't be read, nothing said in the call was learned. Now its end
keeps what was said (`add`), the user's words and hers and nothing else (hers with a
claim's caveat after them, as it was said: `claim_caveat.py`), in her memory file, and
the learning worker reads it as it reads a chat conversation, once the model
is free (`chat/learning_worker.py`): in parts that fit the window, again after a failure
(`learning_retry.py`), and the row goes as soon as its last part has been read. A call's
words are kept only until then. Added in 0.31.1 without a version of the file: an older
build leaves the table alone, and its calls wait for this one.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from app.services.memory.learning_retry import stamp, wait_after
from app.services.memory.models import _now
from app.services.memory.store import MemoryStore

SCHEMA = """CREATE TABLE IF NOT EXISTS call_learning (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ended_at TEXT NOT NULL,
  turns_json TEXT NOT NULL,
  turn_count INTEGER NOT NULL,
  learned_through INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  retry_after TEXT,
  last_error TEXT
)"""
_SPEAKERS = ("user", "assistant")


class CallDue(NamedTuple):
    """A finished call with turns to read, as the worker took it: `learned_through`
    counts the turns already read."""

    call_id: int
    learned_through: int
    attempts: int
    last_error: str | None = None


class CallQueue:
    """Finished calls, oldest first, each until learning has read the whole of it."""

    def __init__(self, memory: MemoryStore, *, clock: Callable[[], datetime] = _now) -> None:
        self._memory = memory
        self._clock = clock

    async def init_schema(self) -> None:
        async with self._memory.transaction() as db:
            await db.execute(SCHEMA)

    async def add(self, history: Iterable[Mapping[str, Any]]) -> bool:
        """Keep a finished call's turns: the user's and hers that have words in them.
        Nothing is kept of a call in which the user said nothing. True when kept."""
        turns = [
            {"role": str(message["role"]), "content": str(message["content"])}
            for message in history
            if message.get("role") in _SPEAKERS and str(message.get("content") or "").strip()
        ]
        if not any(turn["role"] == "user" for turn in turns):
            return False
        async with self._memory.transaction() as db:
            await db.execute(
                "INSERT INTO call_learning (ended_at, turns_json, turn_count) VALUES (?, ?, ?)",
                (stamp(self._clock()), json.dumps(turns), len(turns)),
            )
        return True

    async def next_due(self) -> CallDue | None:
        """The call that ended first and isn't backing off."""
        async with self._memory.db.execute(
            "SELECT id, learned_through, attempts, last_error FROM call_learning "
            "WHERE retry_after IS NULL OR retry_after <= ? ORDER BY id LIMIT 1",
            (stamp(self._clock()),),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return CallDue(int(row[0]), int(row[1]), int(row[2]), row[3])

    async def turns(self, due: CallDue) -> tuple[list[dict[str, str]], int]:
        """The call's turns, oldest first (learning has read the first
        `due.learned_through`), and how many it has in all. No turns once it is gone,
        or when what is kept can't be read (only an edit from outside writes that): `done`
        then lets it go."""
        async with self._memory.db.execute(
            "SELECT turns_json, turn_count FROM call_learning WHERE id = ?", (due.call_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return [], due.learned_through
        total = int(row[1])
        try:
            turns = [_turn(item) for item in json.loads(row[0])]
        except (ValueError, TypeError, KeyError):
            return [], total
        return turns, total

    async def done(self, due: CallDue, *, through: int) -> None:
        """Learning read the call's first `through` turns: the call goes once that is all
        of them, and otherwise waits for its next part."""
        async with self._memory.transaction() as db:
            await db.execute(
                "DELETE FROM call_learning WHERE id = ? AND turn_count <= ?",
                (due.call_id, through),
            )
            await db.execute(
                "UPDATE call_learning SET learned_through = MAX(learned_through, ?), "
                "attempts = 0, retry_after = NULL, last_error = NULL WHERE id = ?",
                (through, due.call_id),
            )

    async def failed(self, due: CallDue, error: str) -> int:
        """Keep the call and wait before the next attempt, saying why this one failed.
        Returns the wait in seconds."""
        wait = wait_after(due.attempts)
        retry = self._clock() + timedelta(seconds=wait)
        async with self._memory.transaction() as db:
            await db.execute(
                "UPDATE call_learning SET attempts = attempts + 1, retry_after = ?, "
                "last_error = ? WHERE id = ?",
                (stamp(retry), error, due.call_id),
            )
        return wait

    async def waiting(self) -> int:
        """How many finished calls learning hasn't finished reading, for diagnostics."""
        async with self._memory.db.execute("SELECT COUNT(*) FROM call_learning") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0


def _turn(item: Any) -> dict[str, str]:
    """A kept turn as the extractor reads it; raises on anything this queue didn't write."""
    role, content = item["role"], item["content"]
    if role not in _SPEAKERS or not isinstance(content, str):
        raise ValueError("not a turn this queue keeps")
    return {"role": role, "content": content}
