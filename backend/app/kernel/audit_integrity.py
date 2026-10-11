"""The audit log's chain, walked and reviewed (the rules are `audit_chain.py`'s).

`verify` walks the whole log against its head and its anchors, and `check_chain` does
so at every start, recording what it found. A break the person has looked at is
accepted by `mark_reviewed`, from the app (`POST /audit/review`) and never as a
capability: nothing she reads or is told can clear her own log. The review anchors
each row that doesn't add up (`capability_audit_anchor`), moves the head to the log's
end, and appends a row saying so, in one transaction, so the log records its own review.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import aiosqlite

from app.kernel import audit_chain
from app.kernel.audit import AS_READ, append
from app.kernel.audit_chain import ChainReport, Head, Review
from app.kernel.capability import Decision
from app.services.memory.migrations import SET_HEAD
from app.services.memory.models import _now
from app.services.memory.store import MemoryStore
from app.telemetry import current_blackbox
from app.telemetry.blackbox import Level

logger = logging.getLogger(__name__)

_ANCHORS = """CREATE TABLE IF NOT EXISTS capability_audit_anchor (
  row_id INTEGER PRIMARY KEY,
  anchor TEXT NOT NULL
)"""
#: What a review's own row names as its capability. Not one she has: only the app asks.
REVIEW = "audit.review"


@dataclass(frozen=True, slots=True)
class Reviewed:
    """What a review accepted: how many rows it anchored, and whether the head moved
    (rows removed from the end). Neither when nothing was broken: then nothing was
    written."""

    anchored: int
    head_moved: bool


class AuditIntegrity:
    """The audit log's chain on the shared memory connection: one walk at a time, reused
    until the file changes, and the review of a break."""

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory
        self._walking = asyncio.Lock()
        # The last walk, with the database state it read (see `verify`).
        self._walked: tuple[tuple[int, int], ChainReport] | None = None

    async def init_schema(self) -> None:
        async with self._memory.transaction() as db:
            await db.execute(_ANCHORS)

    async def verify(self) -> ChainReport:
        """Walk the whole chain against its head and anchors (`audit_chain.verify`),
        hashing off the event loop.

        Read under the write lock: on the shared connection a read can otherwise see a
        row another coroutine has inserted and not yet committed. And in one read
        transaction: another process's write landing between the rows and the head made
        an untouched log read as changed. A walk is reused until the file changes,
        through this connection (`total_changes`) or any other (`data_version`), and
        callers at once share one: `GET /audit/verify` is a GET any web page can send,
        and each must not cost a walk of the whole log.
        """
        async with self._walking:
            async with self._memory.transaction() as db:
                if not db.in_transaction:
                    await db.execute("BEGIN")
                async with db.execute("PRAGMA data_version") as cur:
                    found = await cur.fetchone()
                state = (int(found[0]) if found else 0, db.total_changes)
                if self._walked is not None and self._walked[0] == state:
                    return self._walked[1]
                rows, head, anchors = await _read(db)
            report = await asyncio.to_thread(audit_chain.verify, rows, head, anchors)
            self._walked = (state, report)
            return report

    async def mark_reviewed(self) -> Reviewed:
        """Accept the log as it is (`audit_chain.review`): the anchors replaced, the head
        moved to the chain's end, and a row appended to say so. In one write
        transaction, taken first, so no row lands between the walk and the write. A log
        with nothing broken is left as it is."""
        async with self._walking:
            async with self._memory.transaction() as db:
                if not db.in_transaction:
                    await db.execute("BEGIN IMMEDIATE")
                rows, head, anchors = await _read(db)
                review = await asyncio.to_thread(audit_chain.review, rows, head, anchors)
                if review.whole:
                    return Reviewed(0, False)
                # Where the log ends: its last row's hash can change without it (blanked).
                moved = head is None or review.head.last_id != head.last_id
                await db.execute("DELETE FROM capability_audit_anchor")
                await db.executemany(
                    "INSERT INTO capability_audit_anchor (row_id, anchor) VALUES (?, ?)",
                    list(review.anchors.items()),
                )
                await db.execute(SET_HEAD, (review.head.last_id, review.head.hash))
                await append(db, _review_row(review, moved))
        logger.warning(
            "the activity log was marked as reviewed: %d rows accepted as they are%s",
            len(review.new),
            ", its end moved back" if moved else "",
        )
        current_blackbox().record(
            "warn", "audit.reviewed", anchored=len(review.new), head_moved=moved
        )
        return Reviewed(len(review.new), moved)


async def check_chain(integrity: AuditIntegrity) -> ChainReport | None:
    """Verify the chain once and put the answer in the black box: the report, or None
    when the walk itself failed. Never raises: a start must not fail on a log it could
    still append to."""
    try:
        report = await integrity.verify()
    except Exception as exc:
        logger.exception("could not walk the audit chain")
        current_blackbox().record("error", "audit.chain", ok=False, error=type(exc).__name__)
        return None
    level: Level = "info" if report.ok else "error"
    fields = {"rows": report.rows, "unchained": report.unchained, "broken_at": report.broken_at}
    current_blackbox().record(level, "audit.chain", ok=report.ok, **fields)
    if not report.ok:
        logger.error("the activity log was changed outside Veronica at row %s", report.broken_at)
    return report


def _review_row(review: Review, moved: bool) -> dict[str, Any]:
    """The review's own row: the person's decision, made in the app."""
    first = review.new[0] if review.new else None
    return {
        "ts": _now().isoformat(),
        "capability": REVIEW,
        "actor": "user",
        "decision": Decision.confirmed.value,
        "ok": 1,
        "code": "",
        "detail": "Changes made to this log outside Veronica were marked as reviewed.",
        "args_json": json.dumps({"anchored": len(review.new), "first": first, "end_moved": moved}),
        "duration_ms": 0,
        "confirm_via": "ui",
    }


async def _read(
    db: aiosqlite.Connection,
) -> tuple[list[aiosqlite.Row], Head | None, dict[int, str]]:
    """Every row in id order, the head, and the anchors, inside the caller's transaction."""
    async with db.execute(f"SELECT {AS_READ} FROM capability_audit ORDER BY id") as cur:
        rows = list(await cur.fetchall())
    return rows, await _head(db), await _anchors(db)


async def _head(db: aiosqlite.Connection) -> Head | None:
    async with db.execute("SELECT last_id, hash FROM capability_audit_head WHERE id = 1") as cur:
        found = await cur.fetchone()
    if found is None:
        return None
    last_id, digest = found["last_id"], found["hash"]
    if isinstance(last_id, int) and isinstance(digest, str):
        return Head(last_id, digest)
    return Head(0, "")  # rewritten with values of the wrong kind: matches no chain


async def _anchors(db: aiosqlite.Connection) -> dict[int, str]:
    """The anchors a review left. One of the wrong kind (edited in) is left out: the row
    it names then breaks, as it would without one."""
    async with db.execute("SELECT row_id, anchor FROM capability_audit_anchor") as cur:
        found = await cur.fetchall()
    return {
        row_id: anchor
        for row_id, anchor in ((row["row_id"], row["anchor"]) for row in found)
        if isinstance(row_id, int) and isinstance(anchor, str)
    }
