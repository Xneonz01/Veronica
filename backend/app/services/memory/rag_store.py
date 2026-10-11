"""SQLite + sqlite-vec persistence for RAG documents + chunks (knowledge path).

Separate tables from conversational memory (memory != knowledge) but sharing the
MemoryStore's single connection. Chunk vectors live in a vec0 cosine table, and an
FTS5 index over the same chunks serves keyword search (`rag_fts.py`); both searches join
back to ``rag_documents`` so every hit carries its source title for citation.
"""

from __future__ import annotations

from datetime import datetime

import aiosqlite
import sqlite_vec

from app.services.memory import rag_fts
from app.services.memory.models import RagChunk, RagDocument, RetrievedChunk, _now
from app.services.memory.schema import coerce_distance, ensure_column
from app.services.memory.store import MemoryStore

_RAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  source_path TEXT,
  mime TEXT NOT NULL,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rag_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  ordinal INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  heading_path TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks(document_id);
"""

# What both searches return per chunk; each adds its own FROM and ordering.
_CHUNK_COLUMNS = (
    "c.id, c.document_id, c.ordinal, c.text, c.heading_path, c.created_at, d.title AS doc_title"
)


def _to_document(row: aiosqlite.Row) -> RagDocument:
    return RagDocument(
        id=row["id"],
        title=row["title"],
        source_path=row["source_path"],
        mime=row["mime"],
        chunk_count=row["chunk_count"],
        added_at=datetime.fromisoformat(row["added_at"]),
    )


def _to_retrieved(row: aiosqlite.Row) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=RagChunk(
            id=row["id"],
            document_id=row["document_id"],
            ordinal=row["ordinal"],
            text=row["text"],
            heading_path=row["heading_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
        ),
        document_title=row["doc_title"],
        distance=coerce_distance(row["distance"]),
    )


def _scope(column: str, document_ids: list[int] | None) -> tuple[str, list[object]]:
    """A project's documents as a condition on `column`; nothing when there is no project."""
    if document_ids is None:
        return "", []
    placeholders = ",".join("?" for _ in document_ids)
    return (
        f" AND {column} IN (SELECT id FROM rag_chunks WHERE document_id IN ({placeholders}))",
        list(document_ids),
    )


class RagStore:
    """Documents + chunks + chunk vectors on the shared memory connection."""

    def __init__(self, memory: MemoryStore, embedding_dim: int) -> None:
        self._memory = memory
        self._dim = embedding_dim

    @property
    def _db(self) -> aiosqlite.Connection:
        return self._memory.db

    async def init_schema(self) -> None:
        # RAG lives on the memory store's single connection, so every write here takes
        # the same lock — a bare commit would land another coroutine's half-finished
        # transaction (and its rollback would wipe ours).
        async with self._memory.transaction() as db:
            await db.executescript(_RAG_SCHEMA)
            # Schema 6 (0.22.0): a file from before has the table without it.
            await ensure_column(db, "rag_chunks", "heading_path", "TEXT NOT NULL DEFAULT ''")
            await db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunk_vec USING vec0("
                f"chunk_id integer primary key, embedding float[{self._dim}] "
                "distance_metric=cosine)"
            )
            await db.executescript(rag_fts.SCHEMA)
            await rag_fts.backfill(db)

    async def add_document(
        self,
        title: str,
        source_path: str | None,
        mime: str,
        chunks: list[tuple[int, str, str, list[float]]],
    ) -> int:
        """Insert a document with its (ordinal, text, heading path, embedding) chunks, in
        one transaction: a write cut short (a failure, or the stop that cancels a document
        still being read) leaves nothing, never a listed document with no chunks."""
        now = _now().isoformat()
        async with self._memory.transaction() as db:
            cur = await db.execute(
                "INSERT INTO rag_documents (title, source_path, mime, chunk_count, added_at) "
                "VALUES (?,?,?,?,?)",
                (title, source_path, mime, len(chunks), now),
            )
            document_id = int(cur.lastrowid or 0)
            for ordinal, text, heading_path, embedding in chunks:
                cur = await db.execute(
                    "INSERT INTO rag_chunks (document_id, ordinal, text, created_at, "
                    "heading_path) VALUES (?,?,?,?,?)",
                    (document_id, ordinal, text, now, heading_path),
                )
                await db.execute(
                    "INSERT INTO rag_chunk_vec(chunk_id, embedding) VALUES (?, ?)",
                    (int(cur.lastrowid or 0), sqlite_vec.serialize_float32(embedding)),
                )
        return document_id

    async def get_document(self, document_id: int) -> RagDocument | None:
        async with self._db.execute(
            "SELECT * FROM rag_documents WHERE id = ?", (document_id,)
        ) as cur:
            row = await cur.fetchone()
        return _to_document(row) if row else None

    async def list_documents(self) -> list[RagDocument]:
        async with self._db.execute("SELECT * FROM rag_documents ORDER BY added_at DESC") as cur:
            rows = await cur.fetchall()
        return [_to_document(row) for row in rows]

    async def has_documents(self) -> bool:
        async with self._db.execute("SELECT 1 FROM rag_documents LIMIT 1") as cur:
            return await cur.fetchone() is not None

    async def delete_document(self, document_id: int) -> None:
        # Set-based, one transaction: a half-done delete would orphan chunk vectors, and
        # because `k` is spent before the JOIN, orphans crowd real chunks out of results.
        # The keyword index follows the chunk rows through its delete trigger.
        async with self._memory.transaction() as db:
            await db.execute(
                "DELETE FROM rag_chunk_vec WHERE chunk_id IN "
                "(SELECT id FROM rag_chunks WHERE document_id = ?)",
                (document_id,),
            )
            await db.execute("DELETE FROM rag_chunks WHERE document_id = ?", (document_id,))
            await db.execute("DELETE FROM rag_documents WHERE id = ?", (document_id,))

    async def knn_chunks(
        self, embedding: list[float], k: int, *, document_ids: list[int] | None = None
    ) -> list[RetrievedChunk]:
        """Nearest chunks, optionally restricted to a set of documents (project scope).

        The restriction is a pre-filter inside the vec0 MATCH, not a filter applied to
        the results: `k` is spent before the JOIN, so post-filtering could return two
        chunks when asked for five. Checked against sqlite-vec 0.1.9 that
        `chunk_id IN (SELECT ...)` constrains the search itself.
        """
        if document_ids is not None and not document_ids:
            return []  # a project with no documents retrieves nothing, not everything
        scope, scoped = _scope("v.chunk_id", document_ids)
        async with self._db.execute(
            f"SELECT {_CHUNK_COLUMNS}, v.distance "
            "FROM rag_chunk_vec v JOIN rag_chunks c ON c.id = v.chunk_id "
            "JOIN rag_documents d ON d.id = c.document_id "
            f"WHERE v.embedding MATCH ? AND k = ?{scope} ORDER BY v.distance",
            [sqlite_vec.serialize_float32(embedding), k, *scoped],
        ) as cur:
            rows = await cur.fetchall()
        return [_to_retrieved(row) for row in rows]

    async def keyword_chunks(
        self,
        question: str,
        embedding: list[float],
        k: int,
        *,
        document_ids: list[int] | None = None,
    ) -> list[RetrievedChunk]:
        """The chunks sharing the most telling words with `question`, best BM25 first.

        Each carries its cosine distance to `embedding` as well, so the relevance floor
        judges a keyword hit on the same scale as a nearest one. The project scope is
        inside the search, before the limit, for the reason `knn_chunks` gives.
        """
        match = rag_fts.match_query(question)
        if not match or (document_ids is not None and not document_ids):
            return []
        scope, scoped = _scope("rowid", document_ids)
        async with self._db.execute(
            # Ties broken by id, so the same question ranks the same way every time.
            "WITH hits AS (SELECT rowid AS id, bm25(rag_chunks_fts) AS score "
            f"FROM rag_chunks_fts WHERE rag_chunks_fts MATCH ?{scope} "
            "ORDER BY score, rowid LIMIT ?) "
            # sqlite-vec raises on a NULL vector rather than returning NULL.
            f"SELECT {_CHUNK_COLUMNS}, CASE WHEN v.embedding IS NULL THEN NULL "
            "ELSE vec_distance_cosine(v.embedding, ?) END AS distance "
            "FROM hits JOIN rag_chunks c ON c.id = hits.id "
            "JOIN rag_documents d ON d.id = c.document_id "
            # LEFT: a keyword hit stays one without its vector; its distance reads far.
            "LEFT JOIN rag_chunk_vec v ON v.chunk_id = c.id ORDER BY hits.score, c.id",
            [match, *scoped, k, sqlite_vec.serialize_float32(embedding)],
        ) as cur:
            rows = await cur.fetchall()
        return [_to_retrieved(row) for row in rows]
