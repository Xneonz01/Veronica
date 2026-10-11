"""Low-level persistence helpers for the memory store: DDL, opening, row mapping. The
schema's versions and upgrades are in migrations.py.

Kept apart from store.py, which holds the async query methods. The memory store and
the RAG store both build on these: `coerce_distance` turns sqlite-vec's NULL (a
degenerate query) into "far"; `to_memory` maps a row to the Pydantic model.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import aiosqlite
import sqlite_vec

from app.services.memory import cipher, migrations, upgrade_copy
from app.services.memory.models import Memory

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'user',
  key TEXT,
  content TEXT NOT NULL,
  normalized_content TEXT NOT NULL DEFAULT '',
  importance REAL NOT NULL DEFAULT 0.5,
  confidence REAL NOT NULL DEFAULT 1.0,
  pinned INTEGER NOT NULL DEFAULT 0,
  pending INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'system',
  session_id INTEGER,
  supersedes_id INTEGER,
  archived INTEGER NOT NULL DEFAULT 0,
  access_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_accessed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind, archived);
CREATE INDEX IF NOT EXISTS idx_mem_profile ON memories(kind, key);
"""


def opt_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def coerce_distance(value: float | None) -> float:
    # sqlite-vec returns NULL for a zero/degenerate query vector — treat it as far.
    return float(value) if value is not None else float("inf")


def to_memory(row: aiosqlite.Row) -> Memory:
    return Memory(
        id=row["id"],
        kind=row["kind"],
        key=row["key"],
        content=row["content"],
        normalized_content=row["normalized_content"],
        importance=row["importance"],
        confidence=row["confidence"],
        pinned=bool(row["pinned"]),
        source=row["source"],
        supersedes_id=row["supersedes_id"],
        pending=bool(row["pending"]),
        archived=bool(row["archived"]),
        access_count=row["access_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_accessed_at=opt_dt(row["last_accessed_at"]),
    )


async def ensure_column(db: aiosqlite.Connection, table: str, column: str, decl: str) -> None:
    """Add a column to an existing table if a prior schema version lacks it."""
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        columns = {row["name"] for row in await cur.fetchall()}
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def open_database(path: Path, embedding_dim: int, key: bytes | None) -> aiosqlite.Connection:
    """Open the DB, load sqlite-vec, make the memory tables if missing, and self-heal.
    Through SQLCipher (`cipher.py`), keyed with the data key; with none, plain.

    A file written by a newer build is refused before anything touches it, and one that
    holds anything and is about to be upgraded is copied first (`upgrade_copy`). The
    upgrade itself waits for every store to have made its tables (`migrations.upgrade`).
    The connection is closed again on any failure: left open, its thread would keep the
    process from exiting.

    Lives here rather than in `MemoryStore` because it is all schema work; the store
    keeps the connection lifecycle.
    """
    holds_anything = path.exists() and path.stat().st_size > 0
    db = await cipher.connect(path, key)
    try:
        db.row_factory = cipher.ROW
        found = await migrations.refuse_newer(db, path)
        if holds_anything and found < migrations.LATEST:
            await upgrade_copy.keep(db, path, found, key)
        await db.enable_load_extension(True)
        await db.load_extension(sqlite_vec.loadable_path())
        await db.enable_load_extension(False)
        await db.executescript(SCHEMA)
        await ensure_column(db, "memories", "pending", "INTEGER NOT NULL DEFAULT 0")
        await db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(memory_id integer "
            f"primary key, embedding float[{embedding_dim}] distance_metric=cosine)"
        )
        # Self-heal: drop vectors for rows that must never be retrievable. An interrupted
        # write can leave one behind, which would keep feeding a deleted memory or an
        # unapproved candidate into prompts.
        await db.execute(
            "DELETE FROM memory_vec WHERE memory_id IN "
            "(SELECT id FROM memories WHERE archived = 1 OR pending = 1)"
        )
        await db.commit()
    except BaseException:
        await db.close()
        raise
    return db
