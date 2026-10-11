"""The activity log's hash chain, as the store writes it and walks it.

Every row the store appends links to the one before and moves the chain's head, so an
edit or a deletion made around the append-only triggers is found by the walk, while
rows from before the chain still count.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes_audit import router as audit_router
from app.core.event_bus import InProcessEventBus
from app.kernel import audit_chain
from app.kernel import audit_integrity as integrity_module
from app.kernel.audit import AuditStore
from app.kernel.audit_chain import ChainReport, Head
from app.kernel.audit_integrity import AuditIntegrity, check_chain
from app.kernel.capability import CapabilityResult, Decision
from app.kernel.errors import Code
from app.services.memory import migrations
from app.services.memory.store import MemoryStore
from tests.fakes import OPEN_STORES, FakeEmbedder, build_fake_memory_stack


async def _store(tmp_path: Path) -> tuple[Any, AuditStore, AuditIntegrity]:
    """As a start leaves it: the audit tables made, then the file upgraded."""
    store, _service, _retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    audit, chain = AuditStore(store), AuditIntegrity(store)
    await audit.init_schema()
    await chain.init_schema()
    await migrations.upgrade(store)
    return store, audit, chain


async def _record(audit: AuditStore, n: int = 0, **overrides: Any) -> None:
    payload: dict[str, Any] = {
        "capability": "memory.save",
        "actor": "veronica:chat",
        "decision": Decision.allowed,
        "result": CapabilityResult(ok=True, detail=f"Saved: note “{n}”"),
        "args": {"content": f"User keeps note {n}.", "kind": "fact"},
        "duration_ms": 12 + n,
    }
    payload.update(overrides)
    await audit.record(**payload)


async def _tamper(store: Any, sql: str, *params: Any) -> None:
    """What someone with a SQLite shell can do: drop a trigger and change the table."""
    await store.db.executescript(
        "DROP TRIGGER IF EXISTS audit_no_update; DROP TRIGGER IF EXISTS audit_no_delete;"
    )
    await store.db.execute(sql, params)
    await store.db.commit()


async def test_every_row_links_to_the_one_before(tmp_path: Path) -> None:
    """Refusals, a cancellation's detached row (a `Code` for its code) and a secret the
    redaction blanked all hash the same when read back."""
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)
    await _record(
        audit,
        9,
        decision=Decision.failed,
        result=CapabilityResult(
            ok=False, code=Code.not_found, detail="Nothing I remember matches those words."
        ),
    )
    await _record(audit, 10, args={"password": "hunter2"})
    # SQLite keeps 12.0 in an INTEGER column as 12, which would hash differently.
    await _record(audit, 11, duration_ms=12.0)
    audit.record_detached(
        capability="files.write",
        actor="veronica:voice",
        decision=Decision.cancelled,
        result=CapabilityResult(ok=False, code=Code.capability_failed, detail="Cancelled."),
        args={"path": "C:/notes/todo.md"},
        duration_ms=4,
    )
    await audit.drain()

    report = await chain.verify()

    assert report.ok and report.rows == 7 and report.unchained == 0


async def test_an_edited_row_is_found(tmp_path: Path) -> None:
    store, audit, chain = await _store(tmp_path)
    for n in range(4):
        await _record(audit, n)

    await _tamper(store, "UPDATE capability_audit SET decision = 'denied' WHERE id = 2")

    assert (await chain.verify()).broken_at == 2


async def test_a_deleted_row_breaks_the_link_after_it(tmp_path: Path) -> None:
    store, audit, chain = await _store(tmp_path)
    for n in range(4):
        await _record(audit, n)

    await _tamper(store, "DELETE FROM capability_audit WHERE id = 2")

    assert (await chain.verify()).broken_at == 3


async def test_a_log_from_before_the_chain_keeps_its_rows_and_starts_one(tmp_path: Path) -> None:
    """Schema 3 adds the two columns to a table that has neither, and the old rows are
    counted, not verified (mutation: drop the `ensure_column` calls)."""
    store, _service, _retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await store.db.executescript(
        "CREATE TABLE capability_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "capability TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL, ok INTEGER NOT "
        "NULL DEFAULT 0, code TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', "
        "args_json TEXT NOT NULL DEFAULT '{}', duration_ms INTEGER NOT NULL DEFAULT 0, "
        "confirm_via TEXT); INSERT INTO capability_audit (ts, capability, actor, decision) "
        "VALUES ('2026-09-01T10:00:00+00:00', 'files.read', 'veronica:chat', 'allowed');"
    )
    await store.db.commit()
    audit, chain = AuditStore(store), AuditIntegrity(store)

    await audit.init_schema()
    await chain.init_schema()
    await _record(audit, 1)
    await _record(audit, 2)

    report = await chain.verify()
    assert report.ok and report.rows == 3 and report.unchained == 1


async def test_writers_at_once_still_make_one_chain(tmp_path: Path) -> None:
    """Each reads the last hash and appends under the one write lock (mutation: read
    the last hash outside the transaction)."""
    _store_, audit, chain = await _store(tmp_path)

    await asyncio.gather(*(_record(audit, n) for n in range(20)))

    report = await chain.verify()
    assert report.ok and report.rows == 20


async def test_the_start_up_walk_says_what_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes: list[tuple[str, str, dict[str, Any]]] = []

    class _Box:
        def record(self, level: str, event: str, **fields: Any) -> None:
            notes.append((level, event, fields))

    monkeypatch.setattr(integrity_module, "current_blackbox", lambda: _Box())
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)

    await check_chain(chain)
    await _tamper(store, "UPDATE capability_audit SET detail = 'nothing' WHERE id = 3")
    await check_chain(chain)

    assert notes == [
        ("info", "audit.chain", {"ok": True, "rows": 3, "unchained": 0, "broken_at": None}),
        ("error", "audit.chain", {"ok": False, "rows": 3, "unchained": 0, "broken_at": 3}),
    ]


async def test_the_walk_is_on_the_rest_api(tmp_path: Path) -> None:
    store, audit, chain = await _store(tmp_path)
    for n in range(2):
        await _record(audit, n)
    app = FastAPI()
    app.include_router(audit_router)
    app.state.kernel_runtime = type("Runtime", (), {"audit": audit, "integrity": chain})()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/audit/verify")

    assert response.json() == {"ok": True, "rows": 2, "unchained": 0, "broken_at": None}


async def test_removing_the_newest_rows_is_found_and_stays_found(tmp_path: Path) -> None:
    """The head still names the last row, and the next row links to it, so the break
    outlives the next write (mutation: link to the newest row left in the table)."""
    store, audit, chain = await _store(tmp_path)
    for n in range(4):
        await _record(audit, n)

    await _tamper(store, "DELETE FROM capability_audit WHERE id > 2")
    assert (await chain.verify()).broken_at == 4

    await _record(audit, 5)
    assert (await chain.verify()).broken_at == 5


async def test_an_emptied_log_is_found(tmp_path: Path) -> None:
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)

    await _tamper(store, "DELETE FROM capability_audit")

    report = await chain.verify()
    assert report.rows == 0 and report.broken_at == 3


async def test_blanked_hashes_are_found(tmp_path: Path) -> None:
    """With every hash gone the rows would pass as from before the chain."""
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)

    await _tamper(store, "UPDATE capability_audit SET hash = NULL, prev_hash = NULL")
    await _tamper(store, "UPDATE capability_audit SET decision = 'denied' WHERE id = 2")

    assert (await chain.verify()).broken_at == 3


async def test_removing_the_head_itself_is_found(tmp_path: Path) -> None:
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)

    await _tamper(store, "DELETE FROM capability_audit_head")

    assert (await chain.verify()).broken_at == 1


async def test_a_head_of_the_wrong_kind_matches_nothing(tmp_path: Path) -> None:
    """(mutation: read the head as it is, and the walk raises on it)"""
    store, audit, chain = await _store(tmp_path)
    await _record(audit, 1)

    await _tamper(store, "UPDATE capability_audit_head SET last_id = 'latest'")

    assert (await chain.verify()).broken_at == 1


async def _reopened(tmp_path: Path, store: Any) -> tuple[Any, AuditStore, AuditIntegrity]:
    """The same file at the next start."""
    await store.close()
    again = MemoryStore(tmp_path / "memory.db", FakeEmbedder.DIM)
    await again.init()
    OPEN_STORES.append(again)
    audit, chain = AuditStore(again), AuditIntegrity(again)
    await audit.init_schema()
    await chain.init_schema()
    await migrations.upgrade(again)
    return again, audit, chain


async def test_a_chain_from_before_the_head_is_given_its_end_once(tmp_path: Path) -> None:
    """The upgrade to schema 4 gives a chain written by 0.20.0 its head; from then on,
    removing its newest rows is found (mutation: create the head table empty)."""
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)
    # As 0.20.0 left it: schema 3, no head.
    await store.db.executescript("DROP TABLE capability_audit_head; PRAGMA user_version = 3;")
    await store.db.commit()

    store, audit, chain = await _reopened(tmp_path, store)
    assert (await chain.verify()).ok

    await _tamper(store, "DELETE FROM capability_audit WHERE id = 3")
    assert (await chain.verify()).broken_at == 3


@pytest.mark.parametrize(
    "tamper",
    [
        # The newest rows, then the head's table: re-made at the next start, it was once
        # given whatever rows were left.
        "DELETE FROM capability_audit WHERE id > 1; DROP TABLE capability_audit_head;",
        "DROP TABLE capability_audit; DROP TABLE capability_audit_head;",  # everything
    ],
)
async def test_dropping_the_head_hides_nothing(tmp_path: Path, tamper: str) -> None:
    """(mutation: seed the head whenever its table is missing)"""
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)
    await store.db.executescript(f"DROP TRIGGER audit_no_delete; {tamper}")
    await store.db.commit()

    store, audit, chain = await _reopened(tmp_path, store)

    assert not (await chain.verify()).ok


async def test_the_walk_reads_the_rows_and_the_head_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process's write landing between the two reads made an untouched log
    read as changed (mutation: read them outside one transaction)."""
    _memory, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)
    read_head = integrity_module._head

    async def head_after_another_write(db: Any) -> Head | None:
        async with aiosqlite.connect(tmp_path / "memory.db", timeout=0.2) as other:
            try:
                await other.execute("UPDATE capability_audit_head SET hash = hash || 'x'")
                await other.commit()
            except aiosqlite.OperationalError:
                pass  # locked out: the walk holds its snapshot
        return await read_head(db)

    monkeypatch.setattr(integrity_module, "_head", head_after_another_write)

    assert (await chain.verify()).ok


async def test_two_connections_writing_at_once_still_make_one_chain(tmp_path: Path) -> None:
    """The chat harness writes the same file while the app runs, on a connection the
    store's own lock knows nothing of (mutation: drop `BEGIN IMMEDIATE`)."""
    store, audit, chain = await _store(tmp_path)
    other = MemoryStore(tmp_path / "memory.db", FakeEmbedder.DIM)
    await other.init()
    OPEN_STORES.append(other)
    other_audit = AuditStore(other)
    await other_audit.init_schema()

    await asyncio.gather(*(_record(log, n) for n in range(30) for log in (audit, other_audit)))

    report = await chain.verify()
    assert report.ok and report.rows == 60


async def test_text_that_is_not_utf8_is_a_break_and_still_listed(tmp_path: Path) -> None:
    """Reading it failed the walk, the start-up check and the activity page (mutation:
    read the text as text)."""
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)

    await _tamper(store, "UPDATE capability_audit SET detail = CAST(X'C328' AS TEXT) WHERE id = 2")

    assert (await chain.verify()).broken_at == 2
    assert [row["detail"] for row in await audit.list() if row["id"] == 2] == ["�("]


@pytest.mark.parametrize("change", ["detail = X'00FF'", "duration_ms = 9e999"])
async def test_a_value_no_row_of_ours_holds_is_a_break_not_a_crash(
    tmp_path: Path, change: str
) -> None:
    """Bytes or an infinity crashed the walk, the start-up check with it, and
    `GET /audit/verify` answered 500."""
    store, audit, chain = await _store(tmp_path)
    for n in range(3):
        await _record(audit, n)

    await _tamper(store, f"UPDATE capability_audit SET {change} WHERE id = 2")

    assert (await chain.verify()).broken_at == 2


async def test_a_walk_that_fails_is_recorded_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: only log it, and the black box says nothing about the log)"""
    notes: list[tuple[str, str, dict[str, Any]]] = []

    class _Box:
        def record(self, level: str, event: str, **fields: Any) -> None:
            notes.append((level, event, fields))

    async def unreadable() -> ChainReport:
        raise aiosqlite.DatabaseError("file is not a database")

    monkeypatch.setattr(integrity_module, "current_blackbox", lambda: _Box())
    _memory, audit, chain = await _store(tmp_path)
    monkeypatch.setattr(chain, "verify", unreadable)

    await check_chain(chain)

    assert notes == [("error", "audit.chain", {"ok": False, "error": "DatabaseError"})]


async def test_a_walk_is_reused_until_the_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GET /audit/verify` is a GET any web page can send: callers at once share one
    walk, and a walk stands until this connection or another writes (mutation: walk
    every time)."""
    store, audit, chain = await _store(tmp_path)
    await _record(audit, 1)
    walks: list[Head | None] = []
    walk = audit_chain.verify

    def counted(rows: Any, head: Head | None, anchors: Any = None) -> ChainReport:
        walks.append(head)
        return walk(rows, head, anchors)

    monkeypatch.setattr(audit_chain, "verify", counted)

    await asyncio.gather(*(chain.verify() for _ in range(5)))
    await chain.verify()
    assert len(walks) == 1

    await _record(audit, 2)
    assert (await chain.verify()).rows == 2 and len(walks) == 2

    async with aiosqlite.connect(tmp_path / "memory.db") as other:
        await other.executescript("DROP TRIGGER audit_no_update;")
        await other.execute("UPDATE capability_audit SET detail = 'nothing' WHERE id = 1")
        await other.commit()
    assert (await chain.verify()).broken_at == 1 and len(walks) == 3


async def test_a_detail_utf8_cannot_hold_is_kept_escaped(tmp_path: Path) -> None:
    """A lone surrogate, from a file name Windows allows or JSON from the model, lost
    the whole row (mutation: store the detail as it is)."""
    _memory, audit, chain = await _store(tmp_path)

    await _record(audit, 1, result=CapabilityResult(ok=True, detail="Wrote notes-\udc80.md"))

    rows = await audit.list()
    assert [row["detail"] for row in rows] == ["Wrote notes-\\udc80.md"]
    assert (await chain.verify()).ok
