"""What learning holds a fact against before filing it: what the person asked her to keep
lately, and what learning itself filed lately, on the shared connection.

Composed like `PruneStore`: `MemoryStore` exposes it as `.recent`, and
`LearningController.file` is its caller (`extraction.sift` applies the rules).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the store owns us, so importing it for real would be circular
    from app.services.memory.store import MemoryStore

_PAGE = 200  # the newest this many: a narrow list keeps a wrong match rare


class RecentStore:
    """Recent memories by where they came from, newest first."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    async def asked_to_keep(self, since: datetime) -> list[str]:
        """What the person asked her to keep since `since`, with her tools or on the
        Memory page (`source` user): active facts and preferences. What learning mustn't
        file again in other words (bug 39)."""
        return await self._contents("archived=0 AND pending=0 AND source='user'", since)

    async def learned(self, since: datetime) -> list[str]:
        """The facts and preferences learning filed since `since`, whatever became of
        them: kept, waiting for review, or undone and forgotten since. What a part read
        after the turns before it mustn't file again (bug 40): those turns were learned
        from when they were read, and a memory undone then stays undone."""
        return await self._contents("source='inferred'", since)

    async def _contents(self, which: str, since: datetime) -> list[str]:
        async with self._memory.db.execute(
            f"SELECT content FROM memories WHERE {which} AND kind IN ('fact', 'preference') "
            "AND created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (since.isoformat(), _PAGE),
        ) as cur:
            rows = await cur.fetchall()
        return [row["content"] for row in rows]
