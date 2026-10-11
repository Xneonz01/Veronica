"""memory.db's schema history, and how an older file is brought up to this build.

memory.db holds every table: memories, conversations, documents and the activity log.
Its version is SQLite's `user_version`. Each store makes the tables it owns if they are
missing, at every start (`CREATE TABLE IF NOT EXISTS`, `ensure_column`), which keeps a
file whole even when a table has been lost. What that can't do is here: the history of
versions, so a build refuses a file newer than itself instead of misreading it, and the
steps that must run once, in order, on an older file, such as giving an existing audit
chain its head.

`upgrade` runs once every store has made its tables, and before anything reads them for
a purpose (the audit chain's walk) or writes a row to them: no store may write rows at
its start. Each step runs in its own transaction together with its version, begun
explicitly: sqlite3's legacy mode begins no transaction for DDL or a PRAGMA, so a step
that failed would otherwise leave its change behind and look done. A step may not end
its transaction itself (`executescript` does, with a COMMIT first): it could no longer
be undone, and is refused. A start cut short leaves the file at the last step it
finished.

What the stores add at their start comes before the version that describes it, so a
start cut short in between leaves a file that says an older version but holds a table
or a column more. Only tables and columns, no rows in them, so a build of that older
version reads it as its own, and the next start stamps it. Before the first step on a
file that holds anything, a copy of it is kept (`upgrade_copy.py`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from app.services.memory import upgrade_copy

if TYPE_CHECKING:  # the store opens its file through `schema`, which imports this module
    from app.services.memory.store import MemoryStore

Step = Callable[[aiosqlite.Connection], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Migration:
    """One version: what changed and why an older build must refuse the file, and the
    step that runs once to get there (None when the store that owns the table makes the
    change at every start)."""

    version: int
    reason: str
    apply: Step | None = None


class DatabaseTooNewError(RuntimeError):
    """The database was written by a newer build than this one. `copy` is the newest
    copy from before an upgrade that this build can open, if there is one."""

    def __init__(self, message: str, *, found: int, copy: Path | None = None) -> None:
        super().__init__(message)
        self.found = found
        self.copy = copy


class MigrationError(RuntimeError):
    """A step can't be applied as one transaction."""


# Where the audit log's hash chain ends (`kernel/audit_chain.py`).
AUDIT_HEAD_TABLE = """CREATE TABLE IF NOT EXISTS capability_audit_head (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  last_id INTEGER NOT NULL,
  hash TEXT NOT NULL
)"""
_SEED_HEAD = (
    "INSERT OR IGNORE INTO capability_audit_head (id, last_id, hash) SELECT 1, id, hash "
    "FROM capability_audit WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1"
)
SET_HEAD = "INSERT OR REPLACE INTO capability_audit_head (id, last_id, hash) VALUES (1, ?, ?)"


async def _give_the_chain_its_head(db: aiosqlite.Connection) -> None:
    """An existing chain's end, taken at its word this once. From then on the store moves
    the head with every row it appends, and a head gone missing reads as a removal."""
    await db.execute(AUDIT_HEAD_TABLE)
    async with db.execute("PRAGMA table_info(capability_audit)") as cur:
        chained = "hash" in {row["name"] for row in await cur.fetchall()}
    if chained:
        await db.execute(_SEED_HEAD)


async def ensure_audit_head(db: aiosqlite.Connection) -> None:
    """The audit store's start: make the head if it is missing. A file at schema 4 or
    later had one, so it was dropped, and a head that matches no chain keeps the loss in
    view. Before 4 the chain's end is taken at its word, as the upgrade would, but now:
    a row recorded before the upgrade must link to it."""
    async with db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'capability_audit_head'"
    ) as cur:
        if await cur.fetchone() is not None:
            return
    if await read_version(db) >= 4:
        await db.execute(AUDIT_HEAD_TABLE)
        await db.execute(SET_HEAD, (0, ""))
    else:
        await _give_the_chain_its_head(db)


PLAN: tuple[Migration, ...] = (
    Migration(1, "every table before 0.19.0; a file from before versioning is taken as 1"),
    Migration(
        2,
        "chat_messages.caveats_json (0.19.0, made by ChatStore): an older build would "
        "store replies without their caveats",
    ),
    Migration(
        3,
        "capability_audit.prev_hash and hash (0.20.0, made by AuditStore): an older build "
        "would append rows without a hash and break the chain",
    ),
    Migration(
        4,
        "capability_audit_head (0.21.1): an older build would append rows without moving "
        "the head, which would then read as rows added from outside",
        _give_the_chain_its_head,
    ),
    Migration(
        5,
        "chat_learning (0.21.5, made by ChatStore): an older build would store messages "
        "without marking them, and this one would learn from them a second time",
    ),
    Migration(
        6,
        "rag_chunks.heading_path and the rag_chunks_fts keyword index (0.22.0, made by "
        "RagStore): an older build would index documents without their heading paths",
    ),
)
LATEST = PLAN[-1].version


async def read_version(db: aiosqlite.Connection) -> int:
    async with db.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def refuse_newer(db: aiosqlite.Connection, path: Path) -> int:
    """The file's version, or DatabaseTooNewError before anything touches it: tables made
    "if not exists" say nothing about tables that exist in a newer shape, which an older
    build would read wrongly and write rows to that the newer one doesn't expect."""
    found = await read_version(db)
    if found <= LATEST:
        return found
    copy = upgrade_copy.newest(path, LATEST)
    # SQLite's own files beside it (memory.db-journal) go too: one a crash left would be
    # applied to whatever file is named memory.db next, and damage the copy.
    way_back = (
        f"or go back to the copy from before the update: with Veronica's backend stopped, "
        f"move {path.name} aside with any file beside it whose name starts with "
        f"{path.name}-, and rename {copy.name} to {path.name}."
        if copy is not None
        else "there is no copy from before the update beside it to go back to."
    )
    raise DatabaseTooNewError(
        f"{path.name} was written by a newer version of Veronica (database schema {found}; "
        f"this version understands up to {LATEST}). Update Veronica, {way_back}",
        found=found,
        copy=copy,
    )


async def upgrade(store: MemoryStore) -> int:
    """Bring the store's file to this build's version, once every store on it has made
    its tables; the version it is at. Under the store's write lock, so nothing else
    writes between the steps, and with nothing begun, so each step begins its own."""
    async with store.transaction() as db:
        return await apply_pending(db)


async def apply_pending(db: aiosqlite.Connection, plan: tuple[Migration, ...] = PLAN) -> int:
    """Run each step the file hasn't had, each in its own transaction with its version,
    and return the version the file is at.

    Each step begins its transaction itself, so no transaction may be open on `db`
    (`upgrade` makes sure). The version is read again once the file's write lock is
    taken, since another process on the same file may have moved it on since.
    """
    version = _known(await read_version(db), plan)
    for step in plan:
        if step.version <= version:
            continue
        await db.execute("BEGIN IMMEDIATE")
        try:
            version = _known(await read_version(db), plan)
            if step.version > version:
                if step.apply is not None:
                    await step.apply(db)
                if not db.in_transaction:
                    raise MigrationError(
                        f"Step {step.version} ended its own transaction, so it can't be "
                        "undone; its version isn't stamped."
                    )
                # A PRAGMA takes no bound parameter; the version is this module's own int.
                await db.execute(f"PRAGMA user_version = {int(step.version)}")
                version = step.version
        except BaseException:
            await db.rollback()
            raise
        await db.commit()
    return version


def _known(version: int, plan: tuple[Migration, ...]) -> int:
    """`version`, unless another build moved the file past this plan since it opened."""
    if version > plan[-1].version:
        raise DatabaseTooNewError(
            f"The file moved on to database schema {version} while this version was "
            f"starting; it understands up to {plan[-1].version}.",
            found=version,
        )
    return version
