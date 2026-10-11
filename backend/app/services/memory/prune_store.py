"""The ceiling on what she keeps, on the shared connection: how many memories the cap may
prune, and archiving the least recently used over it.

Composed like `ProfileStore`: `MemoryStore` exposes it as `.pruning`, and
`MemoryService._enforce_cap` is its caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the store owns us, so importing it for real would be circular
    from app.services.memory.store import MemoryStore

# Her profile, what she was asked to pin and what waits for review are never pruned.
_PRUNABLE = "archived=0 AND pending=0 AND pinned=0 AND kind != 'profile'"


class PruneStore:
    """The cap's count and its sweep, sharing the memory store's connection and lock."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    async def count_prunable(self) -> int:
        """Active, non-pinned, non-profile memories: the only set the ceiling prunes."""
        async with self._memory.db.execute(
            f"SELECT COUNT(*) AS n FROM memories WHERE {_PRUNABLE}"
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def prune_oldest(self, limit: int) -> list[int]:
        """Archive the `limit` least-recently-used prunable memories; return their ids.

        LRU = oldest `last_accessed_at` (falling back to `created_at`) first, so a memory
        recalled recently survives. Never touches Tier-1 profile or pinned items.
        """
        if limit <= 0:
            return []
        # One transaction for the whole sweep: a half-pruned batch (rows flagged
        # archived with their vectors still live) would keep feeding KNN.
        async with self._memory.transaction() as db:
            async with db.execute(
                f"SELECT id FROM memories WHERE {_PRUNABLE} "
                "ORDER BY COALESCE(last_accessed_at, created_at) ASC, id ASC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
            ids = [int(row["id"]) for row in rows]
            for memory_id in ids:
                await self._memory.archive_memory(memory_id)  # joins this transaction
        return ids
