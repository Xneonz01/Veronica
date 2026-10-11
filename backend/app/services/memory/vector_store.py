"""Vector writes and the two candidate searches, on the shared connection.

Composed into `MemoryStore` like `ProfileStore`, and exposed as `.vectors`. Search is
a different concern from memory CRUD: `knn` reads the sqlite-vec virtual table,
`lexical_candidates` doesn't touch it, and both serve `MemoryRetriever` rather than
the CRUD callers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

import sqlite_vec

from app.services.memory.models import Memory
from app.services.memory.schema import coerce_distance, to_memory

if TYPE_CHECKING:  # the store owns us, so a real import would be circular
    from app.services.memory.store import MemoryStore


class VectorStore:
    """Embedding writes + KNN/lexical candidate queries."""

    def __init__(self, memory: MemoryStore, embedding_dim: int) -> None:
        self._memory = memory
        self._dim = embedding_dim

    async def set_vector(self, memory_id: int, embedding: list[float]) -> None:
        # Delete + insert must land together, or the memory is briefly committed as
        # active-with-no-vector and silently unretrievable.
        async with self._memory.transaction() as db:
            await db.execute("DELETE FROM memory_vec WHERE memory_id = ?", (memory_id,))
            await db.execute(
                "INSERT INTO memory_vec(memory_id, embedding) VALUES (?, ?)",
                (memory_id, sqlite_vec.serialize_float32(embedding)),
            )

    async def knn(self, embedding: list[float], k: int) -> list[tuple[Memory, float]]:
        """Return the k nearest live memories as (memory, cosine_distance).

        The archived/pending predicate is a backstop, not the primary mechanism: the
        vector row is dropped on archive so `k` stays exact. It is here so that a stray
        vector (say from an interrupted write) can never leak a deleted memory or an
        unapproved candidate into a prompt.
        """
        async with self._memory.db.execute(
            "SELECT m.*, v.distance FROM memory_vec v JOIN memories m ON m.id = v.memory_id "
            "WHERE v.embedding MATCH ? AND k = ? AND m.archived = 0 AND m.pending = 0 "
            "ORDER BY v.distance",
            (sqlite_vec.serialize_float32(embedding), k),
        ) as cur:
            rows = await cur.fetchall()
        return [(to_memory(row), coerce_distance(row["distance"])) for row in rows]

    async def lexical_candidates(self, tokens: Iterable[str], k: int) -> list[Memory]:
        toks = list(tokens)[:8]
        if not toks:
            return []
        likes = " OR ".join("normalized_content LIKE ?" for _ in toks)
        params: list[object] = [f"%{tok}%" for tok in toks]
        params.append(k)
        async with self._memory.db.execute(
            f"SELECT * FROM memories WHERE archived=0 AND pending=0 AND kind != 'profile' "
            f"AND ({likes}) ORDER BY updated_at DESC LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [to_memory(row) for row in rows]
