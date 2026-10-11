"""The audit log: what it records, what it redacts, and that it cannot be rewritten."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlcipher3 import IntegrityError

from app.api.routes_audit import router as audit_router
from app.core.event_bus import InProcessEventBus
from app.kernel.audit import AuditStore
from app.kernel.capability import CapabilityResult, Decision
from app.kernel.redaction import redact_args
from tests.fakes import build_fake_memory_stack


async def _store(tmp_path: Path) -> tuple[Any, AuditStore]:
    store, _service, _retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    audit = AuditStore(store)
    await audit.init_schema()
    return store, audit


async def _record(audit: AuditStore, **overrides: Any) -> None:
    payload: dict[str, Any] = {
        "capability": "files.read",
        "actor": "veronica",
        "decision": Decision.allowed,
        "result": CapabilityResult(ok=True, detail="done"),
        "args": {"path": "C:/notes.txt"},
        "duration_ms": 12,
    }
    payload.update(overrides)
    await audit.record(**payload)


# -- redaction -----------------------------------------------------------------


def test_secret_arguments_are_redacted() -> None:
    # The sensitivity guard is tuned for sentences, so a bare {"password": "hunter2"}
    # matches nothing on its own; redact_args also checks a rebuilt "key is value".
    assert redact_args({"password": "hunter2"})["password"] == "[redacted]"
    assert redact_args({"api_key": "abc123xyz"})["api_key"] == "[redacted]"
    assert redact_args({"note": "the safe passcode is 7741"})["note"] == "[redacted]"


def test_ordinary_arguments_survive_and_bulk_is_truncated() -> None:
    args = redact_args({"path": "C:/projects/readme.md", "content": "x" * 5000})
    assert args["path"] == "C:/projects/readme.md"
    assert len(args["content"]) < 250  # a file body never lands in the log
    assert args["content"].endswith("…")


# -- recording -----------------------------------------------------------------


@pytest.mark.parametrize(
    "decision", [Decision.allowed, Decision.denied, Decision.failed, Decision.confirmed]
)
async def test_every_decision_is_recorded(tmp_path: Path, decision: Decision) -> None:
    store, audit = await _store(tmp_path)
    await _record(audit, decision=decision)
    rows = await audit.list()
    assert len(rows) == 1 and rows[0]["decision"] == decision.value
    await store.close()


async def test_result_data_is_never_stored(tmp_path: Path) -> None:
    # data can hold an entire file body; only ok/code/detail are kept.
    store, audit = await _store(tmp_path)
    await _record(audit, result=CapabilityResult(ok=True, data={"content": "TOP SECRET"}))
    rows = await audit.list()
    assert "TOP SECRET" not in str(rows[0])
    await store.close()


async def test_secrets_in_args_never_reach_the_log(tmp_path: Path) -> None:
    store, audit = await _store(tmp_path)
    await _record(audit, args={"password": "hunter2", "path": "C:/x.txt"})
    rows = await audit.list()
    assert "hunter2" not in rows[0]["args_json"]
    assert "C:/x.txt" in rows[0]["args_json"]  # the useful part survives
    await store.close()


# -- append-only ---------------------------------------------------------------


async def test_the_log_cannot_be_rewritten_or_erased(tmp_path: Path) -> None:
    store, audit = await _store(tmp_path)
    await _record(audit)

    with pytest.raises(IntegrityError):
        await store.db.execute("UPDATE capability_audit SET decision='allowed'")
    with pytest.raises(IntegrityError):
        await store.db.execute("DELETE FROM capability_audit")

    assert await audit.count() == 1  # the row survived both attempts
    await store.close()


async def test_a_failed_audit_write_does_not_raise(tmp_path: Path) -> None:
    # The side effect may already have happened; misreporting it as a failure would be
    # worse than an unrecorded row.
    store, audit = await _store(tmp_path)
    await store.db.execute("DROP TABLE capability_audit")
    await store.db.commit()
    await _record(audit)  # must not raise
    await store.close()


async def test_filtering_and_paging(tmp_path: Path) -> None:
    store, audit = await _store(tmp_path)
    await _record(audit, capability="files.read")
    await _record(audit, capability="files.write", decision=Decision.denied)
    assert len(await audit.list(capability="files.write")) == 1
    assert len(await audit.list(decision="denied")) == 1
    assert len(await audit.list(limit=1)) == 1
    assert await audit.count() == 2
    await store.close()


async def test_the_total_counts_what_the_filters_page_through(tmp_path: Path) -> None:
    """The total counted the whole log, whatever the filters (mutation: count it all)."""
    store, audit = await _store(tmp_path)
    await _record(audit, capability="files.read")
    await _record(audit, capability="files.write", decision=Decision.denied)
    await _record(audit, capability="files.write")
    for filters in ({"capability": "files.write"}, {"decision": "denied"}, {}):
        assert await audit.count(**filters) == len(await audit.list(**filters))
    assert await audit.count(capability="files.write", decision="denied") == 1

    app = FastAPI()
    app.include_router(audit_router)
    app.state.kernel_runtime = SimpleNamespace(audit=audit)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = (await client.get("/audit", params={"capability": "files.write"})).json()
    assert (len(page["rows"]), page["total"]) == (2, 2)
    await store.close()
