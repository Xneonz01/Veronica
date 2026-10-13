"""The audit log: an append-only record of every capability decision.

The row is written and awaited before a result is returned, so an action never
reports success ahead of its own record.

Like `RagStore`, it owns its schema and borrows the memory store's shared
connection, creating the schema inside the store's `transaction()`.

Limits:
  * The UPDATE/DELETE triggers stop this application (and any bug in it) from
    rewriting history. Anyone with a `sqlite3` shell can drop them, so each row also
    carries a hash chained to the row before, and the chain's head is kept beside the
    table (`audit_chain.py`): an edit or a removal made without recomputing every later
    hash and the head is found by the walk (`audit_integrity.py`). The chain has no key,
    so it is not a signature, and the DB is not encrypted.
  * The action and its audit row touch different resources (filesystem vs SQLite),
    so they are not atomic. If the write fails after a side effect we log loudly and
    still return the true result, rather than misreporting a completed action.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

import aiosqlite

from app.core.logging import log_failure
from app.kernel.audit_chain import GENESIS, HASHED_COLUMNS, TEXT_COLUMNS, chain_hash
from app.kernel.capability import CapabilityResult, Decision
from app.kernel.redaction import redact_args, redact_detail
from app.services.memory.migrations import SET_HEAD, ensure_audit_head
from app.services.memory.models import _now
from app.services.memory.schema import ensure_column
from app.services.memory.store import MemoryStore

logger = logging.getLogger(__name__)

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  capability TEXT NOT NULL,
  actor TEXT NOT NULL,
  decision TEXT NOT NULL,
  ok INTEGER NOT NULL DEFAULT 0,
  code TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  args_json TEXT NOT NULL DEFAULT '{}',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  confirm_via TEXT,
  prev_hash TEXT,
  hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON capability_audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_cap ON capability_audit(capability, ts DESC);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON capability_audit
  BEGIN SELECT RAISE(ABORT, 'capability_audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON capability_audit
  BEGIN SELECT RAISE(ABORT, 'capability_audit is append-only'); END;
"""
#: A row's columns as the walk and the activity page read them. Text is read as bytes,
#: so a value that isn't UTF-8 (edited in from outside) reads as a broken row, or shows
#: as damaged, instead of failing the whole read.
AS_READ = ", ".join(
    f"CAST({name} AS BLOB) AS {name}" if name in TEXT_COLUMNS else name
    for name in ("id", *HASHED_COLUMNS, "hash")
)
_INSERT = (
    f"INSERT INTO capability_audit ({', '.join(HASHED_COLUMNS)}, hash) "
    f"VALUES ({', '.join('?' * (len(HASHED_COLUMNS) + 1))})"
)


class AuditStore:
    """Append-only capability audit on the shared memory connection."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def _db(self) -> aiosqlite.Connection:
        return self._memory.db

    async def init_schema(self) -> None:
        # Use the store's transaction: a bare commit here would also commit another
        # coroutine's half-finished transaction.
        async with self._memory.transaction() as db:
            await db.executescript(_AUDIT_SCHEMA)
            # Schema 3 (0.20.0): a table from before the chain has neither column.
            await ensure_column(db, "capability_audit", "prev_hash", "TEXT")
            await ensure_column(db, "capability_audit", "hash", "TEXT")
            await ensure_audit_head(db)

    async def record(
        self,
        *,
        capability: str,
        actor: str,
        decision: Decision,
        result: CapabilityResult,
        args: Mapping[str, Any],
        duration_ms: int,
        confirm_via: str | None = None,
    ) -> None:
        """Append one decision, chained to the row before. `result.data` is never stored.

        The head is read and moved under SQLite's own write lock, taken first: the
        store's lock is this process's alone, and the chat harness writes the same file
        while the app runs. Without it two rows could link to the same predecessor.
        """
        try:
            async with self._memory.transaction() as db:
                if not db.in_transaction:
                    await db.execute("BEGIN IMMEDIATE")
                await append(
                    db,
                    {
                        "ts": _now().isoformat(),
                        "capability": capability,
                        "actor": actor,
                        "decision": decision.value,
                        "ok": int(result.ok),
                        "code": result.code,
                        "detail": redact_detail(result.detail),
                        "args_json": json.dumps(redact_args(args), default=str),
                        "duration_ms": int(duration_ms),
                        "confirm_via": confirm_via,
                    },
                )
        except Exception as exc:
            # The side effect may already have happened; misreporting it as a failure
            # would be worse than an unrecorded row. Log loudly and carry on, by its type
            # and place: its message can quote the arguments the row was keeping.
            log_failure(logger, f"AUDIT WRITE FAILED for {capability} ({decision.value})", exc)

    def record_detached(
        self,
        *,
        capability: str,
        actor: str,
        decision: Decision,
        result: CapabilityResult,
        args: Mapping[str, Any],
        duration_ms: int,
        confirm_via: str | None = None,
    ) -> None:
        """Record from a task being cancelled, without making it wait on the write lock."""
        task = asyncio.create_task(
            self.record(
                capability=capability,
                actor=actor,
                decision=decision,
                result=result,
                args=args,
                duration_ms=duration_ms,
                confirm_via=confirm_via,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Let detached writes finish (called before the DB closes)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        capability: str | None = None,
        decision: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _filtered(capability, decision)
        async with self._db.execute(
            f"SELECT {AS_READ} FROM capability_audit{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ) as cur:
            rows = await cur.fetchall()
        return [
            {key: _shown(row[key]) for key in row.keys()}  # noqa: SIM118 - sqlite3.Row
            for row in rows
        ]

    async def count(self, *, capability: str | None = None, decision: str | None = None) -> int:
        """How many rows `list` would page through with the same filters: the whole log
        was counted, whatever they were."""
        where, params = _filtered(capability, decision)
        async with self._db.execute(
            f"SELECT COUNT(*) AS n FROM capability_audit{where}", params
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0


def _filtered(capability: str | None, decision: str | None) -> tuple[str, list[Any]]:
    """The WHERE clause and its values for the log's filters, both optional."""
    clauses: list[str] = []
    params: list[Any] = []
    if capability:
        clauses.append("capability = ?")
        params.append(capability)
    if decision:
        clauses.append("decision = ?")
        params.append(decision)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


async def append(db: aiosqlite.Connection, fields: Mapping[str, Any]) -> None:
    """Append one row inside the caller's write transaction, chained to the head, which
    then moves to it. `fields` are the hashed columns but `prev_hash`.

    The digest is taken over the values as SQLite hands them back (text, an int for
    `ok` and `duration_ms`), so the walk recomputes the same bytes.
    """
    row = {**fields, "prev_hash": await _chain_end(db)}
    digest = chain_hash(row)
    cursor = await db.execute(_INSERT, (*(row[name] for name in HASHED_COLUMNS), digest))
    await db.execute(SET_HEAD, (cursor.lastrowid, digest))


async def _chain_end(db: aiosqlite.Connection) -> str:
    """What the next row links to: the head's hash, or GENESIS for the chain's first row.
    The head, not the newest row: after a removal the next row still names what was
    removed, so the break outlives it."""
    async with db.execute("SELECT hash FROM capability_audit_head WHERE id = 1") as cur:
        found = await cur.fetchone()
    return str(found["hash"]) if found else GENESIS


def _shown(value: Any) -> Any:
    """A value for the activity page: text that isn't UTF-8 shows as damaged."""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
