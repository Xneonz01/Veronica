"""memory.db's versions: an older file is brought up to this build one step at a time,
each step whole or not at all; a newer file is refused. The copy kept from before an
upgrade is in test_upgrade_copy.py."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.kernel.audit import AuditStore
from app.kernel.audit_integrity import AuditIntegrity
from app.main import create_app
from app.services.chat.attachments import AttachmentStore
from app.services.chat.projects import ProjectStore
from app.services.chat.store import ChatStore
from app.services.memory import migrations
from app.services.memory.migrations import (
    LATEST,
    PLAN,
    DatabaseTooNewError,
    Migration,
    MigrationError,
)
from app.services.memory.store import MemoryStore
from tests.fakes import OPEN_STORES, FakeEmbedder, build_fake_rag, open_memory_file

STORE_TABLES = {
    "memories",
    "capability_audit",
    "capability_audit_head",
    "chat_conversations",
    "chat_messages",
    "chat_learning",
    "chat_attachments",
    "chat_projects",
    "rag_documents",
    "rag_chunks",
    "rag_chunks_fts",
}


def user_version(path: Path) -> int:
    with closing(open_memory_file(path)) as db:
        version: int = db.execute("PRAGMA user_version").fetchone()[0]
    return version


def tables(path: Path) -> set[str]:
    with closing(open_memory_file(path)) as db:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {name for (name,) in rows}


def columns(path: Path, table: str) -> set[str]:
    with closing(sqlite3.connect(path)) as db:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


async def opened(path: Path) -> MemoryStore:
    store = MemoryStore(path, FakeEmbedder.DIM)
    await store.init()
    OPEN_STORES.append(store)
    return store


async def started(path: Path) -> MemoryStore:
    """As a start leaves the file before its upgrade: every store has made its tables."""
    store = await opened(path)
    await AuditStore(store).init_schema()
    await ChatStore(store).init_schema()
    await AttachmentStore(store, path.parent / "attachments").init_schema()
    await ProjectStore(store).init_schema()
    await build_fake_rag(store, InProcessEventBus())
    return store


def test_the_plan_is_every_version_in_order_with_its_reason() -> None:
    assert [step.version for step in PLAN] == list(range(1, LATEST + 1))
    assert all(step.reason.strip() for step in PLAN)


async def test_a_new_database_takes_its_version_once_its_stores_have_their_tables(
    tmp_path: Path,
) -> None:
    """Stamped at the end, when the shape is whole: stamped at the open, a start cut
    short left a file claiming schema 6 without its keyword index."""
    path = tmp_path / "memory.db"
    store = await started(path)
    assert user_version(path) == 0

    assert await migrations.upgrade(store) == LATEST

    assert user_version(path) == LATEST
    assert STORE_TABLES <= tables(path)
    await store.close()


def test_the_app_upgrades_its_file_once_every_store_has_its_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutations: a start that never upgrades; one that upgrades before the stores
    after memory have made theirs)"""
    path = tmp_path / "memory.db"
    monkeypatch.setenv("VERONICA_MEMORY__DB_PATH", str(path))
    seen: list[set[str]] = []
    upgrade = migrations.upgrade

    async def watched(store: MemoryStore) -> int:
        async with store.db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            seen.append({row[0] for row in await cur.fetchall()})
        return await upgrade(store)

    monkeypatch.setattr(migrations, "upgrade", watched)
    get_settings.cache_clear()
    try:
        with TestClient(create_app(), base_url="http://localhost") as client:
            assert client.get("/health").status_code == 200
    finally:
        get_settings.cache_clear()

    assert len(seen) == 1 and STORE_TABLES <= seen[0]
    assert user_version(path) == LATEST


async def test_a_file_the_audit_log_never_opened_is_upgraded_all_the_same(
    tmp_path: Path,
) -> None:
    """A script opens the memory store alone: there is no chain to give a head yet
    (mutation: seed from a table that isn't there)."""
    store = await opened(tmp_path / "memory.db")

    assert await migrations.upgrade(store) == LATEST
    assert "capability_audit_head" in tables(tmp_path / "memory.db")
    await store.close()


def _old_layout(path: Path) -> None:
    """A file as a build from before versioning left it: none of the columns or tables
    added since, and one memory, one message and one document in it."""
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
            " scope TEXT NOT NULL DEFAULT 'user', key TEXT, content TEXT NOT NULL,"
            " normalized_content TEXT NOT NULL DEFAULT '', importance REAL NOT NULL DEFAULT"
            " 0.5, confidence REAL NOT NULL DEFAULT 1.0, pinned INTEGER NOT NULL DEFAULT 0,"
            " source TEXT NOT NULL DEFAULT 'system', session_id INTEGER, supersedes_id"
            " INTEGER, archived INTEGER NOT NULL DEFAULT 0, access_count INTEGER NOT NULL"
            " DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_accessed_at"
            " TEXT);"
            "INSERT INTO memories (kind, content, created_at, updated_at) VALUES"
            " ('fact', 'Likes tea', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
            "CREATE TABLE chat_conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT"
            " NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
            "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id"
            " INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, citations_json"
            " TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);"
            "CREATE TABLE capability_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT"
            " NULL, capability TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL,"
            " ok INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL DEFAULT '', detail TEXT NOT"
            " NULL DEFAULT '', args_json TEXT NOT NULL DEFAULT '{}', duration_ms INTEGER NOT"
            " NULL DEFAULT 0, confirm_via TEXT);"
            "CREATE TABLE rag_documents (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT"
            " NULL, source_path TEXT, mime TEXT NOT NULL, chunk_count INTEGER NOT NULL DEFAULT"
            " 0, added_at TEXT NOT NULL);"
            "CREATE TABLE rag_chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id"
            " INTEGER NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL, created_at TEXT"
            " NOT NULL);"
        )


async def test_a_file_from_before_versioning_is_brought_all_the_way_up(tmp_path: Path) -> None:
    """Every version at once, from the oldest layout, with what it held."""
    path = tmp_path / "memory.db"
    _old_layout(path)

    store = await started(path)
    assert await migrations.upgrade(store) == LATEST
    memories = await store.list_memories()
    await store.close()

    assert [memory.content for memory in memories] == ["Likes tea"]
    assert STORE_TABLES <= tables(path)
    assert {"pending"} <= columns(path, "memories")
    assert {"caveats_json"} <= columns(path, "chat_messages")
    assert {"prev_hash", "hash"} <= columns(path, "capability_audit")
    assert {"heading_path"} <= columns(path, "rag_chunks")
    assert {"project_id"} <= columns(path, "chat_conversations")


async def test_a_database_from_a_newer_build_is_refused_untouched(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE future_table (id INTEGER PRIMARY KEY)")
        db.execute(f"PRAGMA user_version = {LATEST + 1}")
        db.commit()

    store = MemoryStore(path, FakeEmbedder.DIM)
    with pytest.raises(DatabaseTooNewError, match="newer version of Veronica"):
        await store.init()

    assert user_version(path) == LATEST + 1
    assert tables(path) == {"future_table"}  # nothing was created or changed


async def test_a_newer_version_that_appears_while_starting_is_refused(tmp_path: Path) -> None:
    """Another build moved the file on between this one's open and its upgrade
    (mutation: trust any version at the upgrade)."""
    path = tmp_path / "memory.db"
    store = await opened(path)
    with closing(sqlite3.connect(path)) as other:
        other.execute(f"PRAGMA user_version = {LATEST + 1}")
        other.commit()

    with pytest.raises(DatabaseTooNewError, match=f"schema {LATEST + 1}"):
        await migrations.upgrade(store)
    await store.close()


async def test_a_step_that_fails_leaves_nothing_behind_and_the_last_step_kept(
    tmp_path: Path,
) -> None:
    """Begun explicitly: sqlite3's legacy mode begins no transaction for DDL, so the
    table below would survive the failure (mutations: no BEGIN; no rollback)."""

    async def half_done(db: aiosqlite.Connection) -> None:
        await db.execute("CREATE TABLE half_done (id INTEGER PRIMARY KEY)")
        raise RuntimeError("the step failed")

    plan = (Migration(1, "done"), Migration(2, "fails half way", half_done))
    store = await opened(tmp_path / "memory.db")

    with pytest.raises(RuntimeError, match="the step failed"):
        await migrations.apply_pending(store.db, plan)

    assert not store.db.in_transaction  # rolled back, not left open for the next writer
    assert await migrations.read_version(store.db) == 1
    async with store.db.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'") as cur:
        assert await cur.fetchone() is None
    await store.close()


async def test_a_version_that_fails_to_stamp_undoes_its_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step and its version are one transaction (mutation: commit the step first)."""

    async def made(db: aiosqlite.Connection) -> None:
        await db.execute("CREATE TABLE made (id INTEGER PRIMARY KEY)")

    store = await opened(tmp_path / "memory.db")
    execute = aiosqlite.Connection.execute

    def failing_stamp(self: aiosqlite.Connection, sql: str, *args: Any) -> Any:
        if sql.startswith("PRAGMA user_version ="):
            raise sqlite3.OperationalError("disk I/O error")
        return execute(self, sql, *args)

    monkeypatch.setattr(aiosqlite.Connection, "execute", failing_stamp)
    with pytest.raises(sqlite3.OperationalError):
        await migrations.apply_pending(store.db, (Migration(1, "makes a table", made),))
    monkeypatch.undo()

    assert "made" not in tables(tmp_path / "memory.db")
    await store.close()


async def test_a_step_that_ends_its_own_transaction_is_refused(tmp_path: Path) -> None:
    """`executescript` commits first, so its change can't be undone; the version isn't
    stamped over it (mutation: stamp regardless)."""

    async def script(db: aiosqlite.Connection) -> None:
        await db.executescript("CREATE TABLE scripted (id INTEGER PRIMARY KEY);")

    store = await opened(tmp_path / "memory.db")

    with pytest.raises(MigrationError, match="ended its own transaction"):
        await migrations.apply_pending(store.db, (Migration(1, "a script", script),))

    assert user_version(tmp_path / "memory.db") == 0
    await store.close()


async def test_steps_another_process_took_meanwhile_are_not_taken_again(tmp_path: Path) -> None:
    """Another process holds the file's write lock and moves the version on: the steps
    wait for the lock, then read the version again (mutations: trust the first read; a
    deferred BEGIN, which reads before it holds the lock)."""
    taken: list[int] = []

    def step(version: int) -> Migration:
        async def apply(_db: aiosqlite.Connection) -> None:
            taken.append(version)

        return Migration(version, f"step {version}", apply)

    path = tmp_path / "memory.db"
    store = await opened(path)
    other = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    other.execute("PRAGMA user_version = 2")

    upgrading = asyncio.create_task(migrations.apply_pending(store.db, (step(1), step(2), step(3))))
    await asyncio.sleep(0.3)  # the upgrade is waiting for the lock by now
    other.execute("COMMIT")
    other.close()

    assert await upgrading == 3
    assert taken == [3]
    await store.close()


async def _chain_without_its_head(tmp_path: Path, version: int) -> None:
    """A file with three recorded actions, its head table dropped, at `version`."""
    from tests.test_audit_store_chain import _record, _store

    store, audit, _chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)
    await store.db.executescript(
        f"DROP TABLE capability_audit_head; PRAGMA user_version = {version};"
    )
    await store.db.commit()
    await store.close()


@pytest.mark.parametrize(("version", "reads_ok"), [(3, True), (4, False)])
async def test_a_missing_head_is_given_the_chains_end_only_before_version_4(
    tmp_path: Path, version: int, reads_ok: bool
) -> None:
    """Before 4 the chain never had one; from 4 on it was dropped, and seeding it from
    what is left would hide a removal (mutation: the rule at another version)."""
    await _chain_without_its_head(tmp_path, version)

    store = await opened(tmp_path / "memory.db")
    audit, chain = AuditStore(store), AuditIntegrity(store)
    await audit.init_schema()
    await chain.init_schema()
    await migrations.upgrade(store)

    assert (await chain.verify()).ok is reads_ok
    await store.close()


async def test_an_action_recorded_before_the_upgrade_links_to_the_seeded_head(
    tmp_path: Path,
) -> None:
    """On a file from before 4, the audit store's start takes the chain's end at once:
    a row recorded before the upgrade then links to it, and the log reads whole."""
    from tests.test_audit_store_chain import _record

    await _chain_without_its_head(tmp_path, 3)
    store = await opened(tmp_path / "memory.db")
    audit, chain = AuditStore(store), AuditIntegrity(store)
    await audit.init_schema()
    await chain.init_schema()

    await _record(audit, 9)
    await migrations.upgrade(store)

    assert (await chain.verify()).ok
    await store.close()


async def test_every_step_of_the_plan_is_one_transaction(tmp_path: Path) -> None:
    """Each real step runs on a file from before versioning with every store's tables,
    and leaves its transaction open for its version (none uses `executescript`)."""
    path = tmp_path / "memory.db"
    store = await started(path)
    for step in PLAN:
        if step.apply is None:
            continue
        await store.db.execute("BEGIN IMMEDIATE")
        await step.apply(store.db)
        assert store.db.in_transaction, f"step {step.version} ended its own transaction"
        await store.db.rollback()
    await store.close()
