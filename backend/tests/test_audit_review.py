"""A break in the activity log, reviewed (`kernel/audit_integrity.py`): the log is
accepted as it is, every later change is still found, and the review is itself a row.
And the break as the app meets it: a problem from the start's walk, gone from every
surface once the person marks it as reviewed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import startup
from app.api.routes_audit import router as audit_router
from app.api.routes_events import AppEventForwarder
from app.core.event_bus import InProcessEventBus
from app.core.problems import PROBLEMS_CHANGED, Problem
from app.kernel.audit import AuditStore
from app.kernel.audit_integrity import REVIEW, AuditIntegrity, Reviewed
from app.schemas.events import Event
from tests.fakes import build_fake_kernel_runtime, build_fake_memory_runtime
from tests.test_audit_store_chain import _record, _store, _tamper


async def _logged(tmp_path: Path, rows: int) -> tuple[Any, AuditStore, AuditIntegrity]:
    store, audit, chain = await _store(tmp_path)
    for n in range(rows):
        await _record(audit, n)
    return store, audit, chain


async def test_a_reviewed_edit_is_accepted_and_the_review_is_logged(tmp_path: Path) -> None:
    """(mutations: no row for the review; accept without anchoring)"""
    store, audit, chain = await _logged(tmp_path, 4)
    await _tamper(store, "UPDATE capability_audit SET detail = 'nothing' WHERE id = 2")
    assert (await chain.verify()).broken_at == 2

    reviewed = await chain.mark_reviewed()

    assert reviewed == Reviewed(anchored=1, head_moved=False)
    report = await chain.verify()
    assert report.ok and report.rows == 5
    (newest,) = await audit.list(limit=1)
    assert (newest["capability"], newest["actor"], newest["decision"]) == (
        REVIEW,
        "user",
        "confirmed",
    )
    assert json.loads(newest["args_json"]) == {"anchored": 1, "first": 2, "end_moved": False}


async def test_a_row_changed_again_after_its_review_breaks_again(tmp_path: Path) -> None:
    """And reviewed again, is accepted again (mutations: take an anchored row whatever
    it holds; keep the anchors a review replaces)."""
    store, _audit, chain = await _logged(tmp_path, 3)
    await _tamper(store, "UPDATE capability_audit SET detail = 'nothing' WHERE id = 2")
    await chain.mark_reviewed()

    await _tamper(store, "UPDATE capability_audit SET detail = 'something else' WHERE id = 2")
    assert (await chain.verify()).broken_at == 2

    assert await chain.mark_reviewed() == Reviewed(anchored=1, head_moved=False)
    assert (await chain.verify()).ok


async def test_a_row_removed_before_an_anchored_one_breaks_again(tmp_path: Path) -> None:
    """An anchor holds the link before its row too: the chain up to it must be as
    reviewed (mutation: anchor the row alone)."""
    store, _audit, chain = await _logged(tmp_path, 5)
    await _tamper(store, "UPDATE capability_audit SET detail = 'nothing' WHERE id = 3")
    await chain.mark_reviewed()

    await _tamper(store, "DELETE FROM capability_audit WHERE id = 2")

    assert (await chain.verify()).broken_at == 3


async def test_rows_removed_from_the_end_move_the_head_back(tmp_path: Path) -> None:
    """And what is recorded next links to it (mutation: leave the head where it was)."""
    store, audit, chain = await _logged(tmp_path, 4)
    await _tamper(store, "DELETE FROM capability_audit WHERE id > 2")
    assert (await chain.verify()).broken_at == 4

    assert await chain.mark_reviewed() == Reviewed(anchored=0, head_moved=True)
    await _record(audit, 9)

    report = await chain.verify()
    assert report.ok and report.rows == 4  # two kept, the review, the new one


async def test_an_emptied_log_reviewed_starts_its_chain_again(tmp_path: Path) -> None:
    store, audit, chain = await _logged(tmp_path, 3)
    await _tamper(store, "DELETE FROM capability_audit")

    await chain.mark_reviewed()
    await _record(audit, 9)

    report = await chain.verify()
    assert report.ok and report.rows == 2


async def test_blanked_hashes_are_accepted_and_what_follows_is_judged_as_before(
    tmp_path: Path,
) -> None:
    """A row with no hash of its own passes on its anchor, which the head then names.
    Passed on as nothing, the chain would read as not begun, and the next row without
    one as from before it (mutation: pass nothing on)."""
    store, audit, chain = await _logged(tmp_path, 4)
    await _tamper(store, "UPDATE capability_audit SET hash = NULL WHERE id >= 3")

    assert await chain.mark_reviewed() == Reviewed(anchored=2, head_moved=False)
    assert (await chain.verify()).ok
    await _record(audit, 9)
    assert (await chain.verify()).ok

    await _tamper(store, "UPDATE capability_audit SET detail = 'nothing' WHERE id = 6")
    assert (await chain.verify()).broken_at == 6


async def test_text_that_is_not_utf8_can_be_reviewed(tmp_path: Path) -> None:
    store, _audit, chain = await _logged(tmp_path, 3)
    await _tamper(store, "UPDATE capability_audit SET detail = CAST(X'C328' AS TEXT) WHERE id = 2")

    await chain.mark_reviewed()

    assert (await chain.verify()).ok


async def test_a_whole_log_is_left_as_it_is(tmp_path: Path) -> None:
    """Nothing to accept: no anchor, no row (mutation: write whatever the walk found)."""
    _store_, audit, chain = await _logged(tmp_path, 2)

    assert await chain.mark_reviewed() == Reviewed(anchored=0, head_moved=False)
    assert await audit.count() == 2


async def test_only_the_app_can_review_the_log(tmp_path: Path) -> None:
    """Not a capability: nothing she reads or is told can clear her own log (mutation:
    register it as one)."""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    kernel = await build_fake_kernel_runtime(memory, InProcessEventBus())

    assert REVIEW not in {capability.name for capability in kernel.registry}
    await memory.store.close()


class _Published:
    def __init__(self) -> None:
        self.types: list[str] = []

    async def publish(self, event: Event) -> None:
        self.types.append(event.type)


async def test_a_break_the_start_finds_is_shown_and_a_review_clears_it(tmp_path: Path) -> None:
    """Found in the background, after the app may have read the problems, so both
    changes are announced (mutations: no problem; no announcement; keep it after the
    review)."""
    store, audit, chain = await _logged(tmp_path, 3)
    await _tamper(store, "UPDATE capability_audit SET detail = 'nothing' WHERE id = 2")
    bus = _Published()
    app = FastAPI()
    app.include_router(audit_router)
    app.state.problems = [Problem(code="models_missing", detail={"models": "turn"})]
    app.state.event_bus = bus
    app.state.kernel_runtime = SimpleNamespace(audit=audit, integrity=chain)

    await startup.check_audit(app, chain)

    assert [p.code for p in app.state.problems] == ["models_missing", "audit_broken"]
    assert app.state.problems[-1].detail == {"row": 2} and bus.types == [PROBLEMS_CHANGED]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/audit/review")

    assert response.json() == {
        "ok": True,
        "rows": 4,
        "unchained": 0,
        "broken_at": None,
        "anchored": 1,
        "end_moved": False,
    }
    assert [p.code for p in app.state.problems] == ["models_missing"]
    assert bus.types == [PROBLEMS_CHANGED, PROBLEMS_CHANGED]


async def test_a_whole_log_at_the_start_is_no_problem(tmp_path: Path) -> None:
    _store_, _audit, chain = await _logged(tmp_path, 2)
    bus = _Published()
    app = SimpleNamespace(state=SimpleNamespace(problems=[], event_bus=bus))

    await startup.check_audit(app, chain)  # type: ignore[arg-type]

    assert app.state.problems == [] and bus.types == []


async def test_the_event_lane_tells_every_surface(tmp_path: Path) -> None:
    """(mutation: leave it off the lane's list)"""
    sent: list[str] = []

    class _WS:
        async def send_text(self, text: str) -> None:
            sent.append(text)

    forwarder = AppEventForwarder(_WS())  # type: ignore[arg-type]
    await forwarder(Event(type=PROBLEMS_CHANGED, source="app", payload={}))
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.001)

    assert [json.loads(frame) for frame in sent] == [{"type": "problems_changed"}]
    await forwarder.aclose()
