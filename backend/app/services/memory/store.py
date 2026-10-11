"""SQLite + sqlite-vec persistence for conversational memory.

Checked against sqlite-vec 0.1.9: the extension loads via
``sqlite_vec.loadable_path()``; vectors live in a ``vec0`` virtual table with a
cosine metric; KNN is ``WHERE embedding MATCH ? AND k = ? ORDER BY distance``.
Because ``k`` is applied before any join filter, archiving a memory deletes its
vector row, so the vector table only holds live, retrievable memories and the ``k``
nearest are exact. Profile rows carry no vector.

One aiosqlite connection per store (single process, single user). Writes are not
serialized for us: sqlite3's legacy transaction mode gives every coroutine on the
connection one shared implicit transaction, so an unrelated ``commit()`` can land
another coroutine's half-finished multi-statement write, and a cancelled task leaves
the rest of its write pending. The session-end summary and the learning extractor run
concurrently (and the user may be editing on the Memory page at the same moment), so
every multi-statement write goes through ``transaction()``, which holds a lock for the
whole sequence and shields the final commit/rollback from cancellation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

import aiosqlite

from app.paths import anchored
from app.services.memory.models import Memory, MemoryCreate, MemoryKind, _now
from app.services.memory.normalize import normalize
from app.services.memory.profile_store import ProfileStore
from app.services.memory.prune_store import PruneStore
from app.services.memory.recent_store import RecentStore
from app.services.memory.schema import open_database, to_memory
from app.services.memory.vector_store import VectorStore

# The stores whose `transaction()` this task is inside, by id. Task-local so a nested
# call joins the outer one instead of deadlocking on the lock; a plain attribute would
# let an unrelated task slip inside someone else's transaction. Per store, because two
# stores on the same file (the chat harness beside the app) each need their own lock
# and their own commit.
_IN_TRANSACTION: ContextVar[frozenset[int]] = ContextVar(
    "memory_store_in_transaction", default=frozenset()
)
_CLOSING = ".!?…"  # what may end a memory without making it a different one


class MemoryStore:
    """Async SQLite store for memories + their vectors (one connection)."""

    def __init__(
        self, db_path: str | Path, embedding_dim: int, *, key: bytes | None = None
    ) -> None:
        self._path = anchored(db_path)
        self._dim = embedding_dim
        self._key = key  # the data key (`core/data_key.py`); None: a plain file
        self._db: aiosqlite.Connection | None = None
        self._closed = False
        self._write_lock = asyncio.Lock()
        # Profile/session rows, vectors, the cap and learning's recent reads share this
        # connection and its lock.
        self.profile = ProfileStore(self)
        self.vectors = VectorStore(self, embedding_dim)
        self.pruning = PruneStore(self)
        self.recent = RecentStore(self)

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError(
                "MemoryStore is closed" if self._closed else "MemoryStore.init() was not awaited"
            )
        return self._db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run a write sequence as one atomic unit, serialized against other writers.

        Nested calls join the outer transaction (one commit at the end). The commit and
        rollback are shielded so a task cancelled mid-write (barge-in, WS disconnect,
        shutdown) still resolves its transaction instead of leaving it for whoever
        commits next.
        """
        inside = _IN_TRANSACTION.get()
        if id(self) in inside:
            yield self.db
            return
        async with self._write_lock:
            token = _IN_TRANSACTION.set(inside | {id(self)})
            try:
                yield self.db
            except BaseException:
                await asyncio.shield(self.db.rollback())
                raise
            else:
                await asyncio.shield(self.db.commit())
            finally:
                _IN_TRANSACTION.reset(token)

    async def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await open_database(self._path, self._dim, self._key)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            self._closed = True

    # -- memories --

    async def insert_memory(self, create: MemoryCreate) -> Memory:
        now = _now().isoformat()
        async with self.transaction() as db:
            cur = await db.execute(
                "INSERT INTO memories (kind, key, content, normalized_content, importance, "
                "confidence, pinned, pending, source, supersedes_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    create.kind.value,
                    create.key,
                    create.content,
                    normalize(create.content),
                    create.importance,
                    create.confidence,
                    int(create.pinned),
                    int(create.pending),
                    create.source,
                    create.supersedes_id,
                    now,
                    now,
                ),
            )
        memory = await self.get_memory(int(cur.lastrowid or 0))
        if memory is None:  # pragma: no cover - the row was just inserted
            raise RuntimeError("inserted memory vanished")
        return memory

    async def get_memory(self, memory_id: int) -> Memory | None:
        async with self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)) as cur:
            row = await cur.fetchone()
        return to_memory(row) if row else None

    async def update_memory(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        pinned: bool | None = None,
    ) -> Memory | None:
        current = await self.get_memory(memory_id)
        if current is None:
            return None
        new_content = current.content if content is None else content
        async with self.transaction() as db:
            await db.execute(
                "UPDATE memories SET content=?, normalized_content=?, importance=?, confidence=?, "
                "pinned=?, updated_at=? WHERE id=?",
                (
                    new_content,
                    normalize(new_content),
                    current.importance if importance is None else importance,
                    current.confidence if confidence is None else confidence,
                    int(current.pinned if pinned is None else pinned),
                    _now().isoformat(),
                    memory_id,
                ),
            )
        return await self.get_memory(memory_id)

    async def archive_memory(self, memory_id: int) -> None:
        """Soft-archive + drop the vector row so it stops being retrieved.

        Both statements must land together: a half-applied archive leaves the row
        flagged archived with its vector intact, and KNN keeps serving a deleted memory.
        """
        async with self.transaction() as db:
            await db.execute(
                "UPDATE memories SET archived=1, updated_at=? WHERE id=?",
                (_now().isoformat(), memory_id),
            )
            await db.execute("DELETE FROM memory_vec WHERE memory_id=?", (memory_id,))

    async def archive_candidate(self, memory_id: int) -> bool:
        """Archive `memory_id` only while it waits for review, in one statement, so an
        approval can't land between a check and this; whether it was waiting. A
        candidate has no vector to drop."""
        async with self.transaction() as db:
            cur = await db.execute(
                "UPDATE memories SET archived=1, updated_at=? "
                "WHERE id=? AND pending=1 AND archived=0",
                (_now().isoformat(), memory_id),
            )
        return cur.rowcount == 1

    async def mark_active(self, memory_id: int) -> bool:
        """Clear the pending flag (a learning candidate is approved) only while it still
        waits for review, as `archive_candidate` checks; whether it did."""
        async with self.transaction() as db:
            cur = await db.execute(
                "UPDATE memories SET pending=0, updated_at=? "
                "WHERE id=? AND pending=1 AND archived=0",
                (_now().isoformat(), memory_id),
            )
        return cur.rowcount == 1

    async def unarchive_memory(self, memory_id: int) -> None:
        """Bring a soft-archived memory back (Undo). The caller re-embeds the vector."""
        async with self.transaction() as db:
            await db.execute(
                "UPDATE memories SET archived=0, updated_at=? WHERE id=?",
                (_now().isoformat(), memory_id),
            )

    async def list_memories(
        self,
        *,
        kind: MemoryKind | None = None,
        pending: bool | None = None,
        include_archived: bool = False,
        limit: int = 200,
    ) -> list[Memory]:
        clauses: list[str] = []
        params: list[object] = []
        if not include_archived:
            clauses.append("archived = 0")
        if pending is not None:
            clauses.append("pending = ?")
            params.append(int(pending))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        async with self.db.execute(
            f"SELECT * FROM memories{where} ORDER BY updated_at DESC LIMIT ?", params
        ) as cur:
            rows = await cur.fetchall()
        return [to_memory(row) for row in rows]

    async def bump_access(self, ids: Iterable[int]) -> None:
        id_list = list(ids)
        if not id_list:
            return
        marks = ",".join("?" for _ in id_list)
        async with self.transaction() as db:
            await db.execute(
                f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? "
                f"WHERE id IN ({marks})",
                (_now().isoformat(), *id_list),
            )

    async def find_active_duplicate(self, content: str) -> Memory | None:
        """An active, non-pending, non-profile memory with identical normalized content.

        Normalized equality only (no fuzzy match) — enough to stop the extractor from
        stacking the same observation across turns, with no false positives. A closing
        full stop doesn't count: "Likes tea." and "Likes tea" were both being kept.
        """
        return await self._same_content(content, "pending=0 AND kind != 'profile'", ())

    async def find_waiting_candidate(self, content: str, key: str | None) -> Memory | None:
        """A candidate waiting for review that says the same, for the same profile slot
        (`key`; None for a fact), compared as `find_active_duplicate` compares."""
        return await self._same_content(content, "pending=1 AND key IS ?", (key,))

    async def _same_content(
        self, content: str, which: str, params: tuple[str | None, ...]
    ) -> Memory | None:
        needle = normalize(content).rstrip(_CLOSING)
        if not needle:
            return None
        # `which` is one of the two fixed conditions above, never caller text.
        async with self.db.execute(
            f"SELECT * FROM memories WHERE archived=0 AND {which} "
            "AND rtrim(normalized_content, ?) = ? ORDER BY id LIMIT 1",
            (*params, _CLOSING, needle),
        ) as cur:
            row = await cur.fetchone()
        return to_memory(row) if row else None
