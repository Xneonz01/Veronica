"""Tier-1 profile slots and session rows, on the shared connection.

A Tier-1 key/value slot is a different concern from memory CRUD and vector search, so
it lives in its own class that borrows the memory store's connection (composed, like
`RagStore` and `ChatStore`, rather than inherited).

`MemoryStore` exposes it as `.profile`, so callers read `store.profile.get_profile()`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.memory.models import Memory, MemoryCreate, MemoryKind, _now
from app.services.memory.normalize import normalize

if TYPE_CHECKING:  # the store owns us, so importing it for real would be circular
    from app.services.memory.store import MemoryStore


class ProfileStore:
    """Profile + session rows, sharing the memory store's connection and write lock."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    # -- profile (Tier-1 key/value; deterministic, never vector-retrieved) --

    async def get_profile(self) -> dict[str, str]:
        async with self._memory.db.execute(
            "SELECT key, content FROM memories WHERE kind='profile' AND archived=0 "
            "AND pending=0 AND key IS NOT NULL ORDER BY updated_at"
        ) as cur:
            rows = await cur.fetchall()
        return {row["key"]: row["content"] for row in rows}

    async def upsert_profile(self, key: str, value: str, *, source: str = "user") -> Memory:
        """Set a Tier-1 slot, keeping one row per key.

        The select and the write share one transaction. Otherwise two writers (say the
        in-call address hook and the session-end extractor) could both see no row and
        both insert, duplicating the slot.
        """
        now = _now().isoformat()
        async with self._memory.transaction() as db:
            async with db.execute(
                "SELECT id FROM memories WHERE kind='profile' AND key=? "
                "AND archived=0 AND pending=0",
                (key,),
            ) as cur:
                existing = await cur.fetchone()
            if existing is None:
                return await self._memory.insert_memory(  # joins this transaction
                    MemoryCreate(
                        kind=MemoryKind.profile,
                        key=key,
                        content=value,
                        importance=1.0,
                        pinned=True,
                        source=source,
                    )
                )
            await db.execute(
                "UPDATE memories SET content=?, normalized_content=?, source=?, updated_at=? "
                "WHERE id=?",
                (value, normalize(value), source, now, existing["id"]),
            )
        updated = await self._memory.get_memory(existing["id"])
        if updated is None:  # pragma: no cover
            raise RuntimeError("profile row vanished")
        return updated
