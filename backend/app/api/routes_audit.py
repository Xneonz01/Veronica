"""Audit REST: the record of every capability request, including refusals.

The log is append-only, so there is no edit or delete route. The one write is the
person's review of a break (`POST /audit/review`), which the log records like any row.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.problems import announce
from app.kernel.audit_chain import ChainReport
from app.kernel.runtime import KernelRuntime

router = APIRouter(prefix="/audit", tags=["audit"])


def _kernel(request: Request) -> KernelRuntime:
    runtime = getattr(request.app.state, "kernel_runtime", None)
    if runtime is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="The activity log is unavailable.")
    kernel: KernelRuntime = runtime
    return kernel


def _report(report: ChainReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "rows": report.rows,
        "unchained": report.unchained,
        "broken_at": report.broken_at,
    }


@router.get("")
async def list_audit(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    capability: str | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    store = _kernel(request).audit
    rows = await store.list(limit=limit, offset=offset, capability=capability, decision=decision)
    return {"rows": rows, "total": await store.count(capability=capability, decision=decision)}


@router.get("/verify")
async def verify_audit(request: Request) -> dict[str, Any]:
    """Walk the log's hash chain: whether every row still adds up, and the first that
    doesn't (`kernel/audit_chain.py`)."""
    return _report(await _kernel(request).integrity.verify())


@router.post("/review")
async def review_audit(request: Request) -> dict[str, Any]:
    """Accept the log as it is: a break the person has looked at stops being reported,
    and the review is itself a row of the log (`kernel/audit_integrity.py`). Never a
    capability, and the origin check keeps web pages from it. With the log whole again,
    the problem goes from every surface."""
    integrity = _kernel(request).integrity
    reviewed = await integrity.mark_reviewed()
    report = await integrity.verify()
    problems = request.app.state.problems
    if report.ok and any(problem.code == "audit_broken" for problem in problems):
        problems[:] = [problem for problem in problems if problem.code != "audit_broken"]
        await announce(request.app.state.event_bus)
    return {**_report(report), "anchored": reviewed.anchored, "end_moved": reviewed.head_moved}
