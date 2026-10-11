"""Recovery key REST (ADR 0001): whether one is made, making one, and using one.

A new recovery key is in one response only, never stored, logged or cached
(`Cache-Control: no-store`). Using one where Windows couldn't open her data keys wraps
them for this account; the start that follows opens them, so the notices that she is
locked give way to one that says to restart (`data_recovered`), on every surface at
once. The origin check keeps web pages from all of it; another program on this computer
can still call it, like the rest of the API (SECURITY.md). Who asked is said: the app
sends its origin and a program doesn't, so her activity log records a key made by a
program as that, and every window warns of it (`recovery.updated`, `via`). While the
start found her memory or voices locked, none is made: its sealing would open nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.refusals import Refusal
from app.core.problems import Problem, announce
from app.core.recovery_key import NotARecoveryKey
from app.kernel.capability import CapabilityResult, Decision
from app.schemas.events import Event
from app.services.recovery import NoneMade, RecoveryKeys, Unavailable, Used, Wrong
from app.telemetry import current_blackbox

router = APIRouter(prefix="/recovery-key", tags=["recovery"])

_UNAVAILABLE = {
    "unsupported": (
        "A recovery key needs Windows, which keeps her keys. On this system her data "
        "isn't encrypted, so there is nothing for one to open."
    ),
    "locked": (
        "Her memory or voices are locked on this computer, so a recovery key can't be "
        "made until they open."
    ),
    "nothing": "There is nothing encrypted yet for a recovery key to open.",
}
_INVALID = (
    "That isn't a recovery key. Check it for a typo: it has 36 letters and digits, in "
    "groups of four."
)
_NONE_MADE = "No recovery key was made for her memory or voices, so none can open them."
_WRONG = (
    "That recovery key doesn't open her memory or voices. It may be one made before the newest."
)
# The notices a key file opened for this account gives way to, by store.
_LOCKED = {"memory": "memory_locked", "voices": "voices_locked"}
# Who asked: the app, which sends its origin (the origin check allows only its own),
# or a program on this computer, which sends none.
_BY_APP = ("user", "ui")
_BY_PROGRAM = ("program", "api")


class UseBody(BaseModel):
    key: str = Field(max_length=200)


@router.get("")
async def recovery_status(request: Request) -> dict[str, Any]:
    status = await asyncio.to_thread(_keys(request).status, locked=_locked(request))
    return {"made": status.made, "partial": status.partial, "unavailable": status.unavailable}


@router.post("")
async def make_recovery_key(request: Request, response: Response) -> dict[str, str]:
    """A new recovery key, shown this once: the one before stops opening her data."""
    response.headers["Cache-Control"] = "no-store"
    try:
        written, made = await asyncio.to_thread(_keys(request).make, locked=_locked(request))
    except Unavailable as exc:
        raise Refusal(409, f"recovery_{exc.reason}", _UNAVAILABLE[exc.reason]) from exc
    current_blackbox().record("info", "recovery.made", via=_who(request)[1])
    await _updated(request, made=made)
    await _audit(
        request,
        "recovery_key.made",
        "A recovery key was made; any before it no longer opens her data.",
    )
    return {"key": written, "made": made}


@router.post("/use")
async def use_recovery_key(request: Request, body: UseBody) -> dict[str, list[str]]:
    """Open her data keys with a recovery key: those Windows couldn't unwrap here are
    wrapped for this account, and a start opens them."""
    try:
        used = await asyncio.to_thread(_keys(request).use, body.key)
    except NotARecoveryKey as exc:
        raise Refusal(422, "recovery_key_invalid", _INVALID) from exc
    except Unavailable as exc:
        raise Refusal(409, f"recovery_{exc.reason}", _UNAVAILABLE[exc.reason]) from exc
    except NoneMade as exc:
        raise Refusal(404, "recovery_key_none", _NONE_MADE) from exc
    except Wrong as exc:
        current_blackbox().record("warn", "recovery.used", outcome="wrong")
        raise Refusal(422, "recovery_key_wrong", _WRONG) from exc
    current_blackbox().record(
        "info", "recovery.used", restored=len(used.restored), opened=len(used.opened)
    )
    if used.restored:
        await _recovered(request, used)
        await _updated(request)
    await _audit(request, "recovery_key.used", _used_words(used))
    return {"restored": used.restored, "opened": used.opened, "unmatched": used.unmatched}


async def _updated(request: Request, *, made: str | None = None) -> None:
    """Every surface reads the recovery key's state again; a key `made` says when, and
    whether the app or a program asked (`via`), so a key nobody chose is seen."""
    payload: dict[str, Any] = {"via": _who(request)[1]}
    if made is not None:
        payload["made"] = made
    await request.app.state.event_bus.publish(
        Event(type="recovery.updated", source="recovery", payload=payload)
    )


def _who(request: Request) -> tuple[str, str]:
    return _BY_APP if request.headers.get("origin") else _BY_PROGRAM


def _locked(request: Request) -> bool:
    """Whether the start found her memory or voices locked (any reason, a key that
    unwraps but opens nothing among them)."""
    problems: list[Problem] = request.app.state.problems
    return any(problem.code in _LOCKED.values() for problem in problems)


def _keys(request: Request) -> RecoveryKeys:
    return RecoveryKeys.of(request.app.state.settings)


async def _recovered(request: Request, used: Used) -> None:
    """Her locked notices give way to one that says to restart, which opens them."""
    problems: list[Problem] = request.app.state.problems
    gone = {_LOCKED[name] for name in used.restored}
    earlier = next((p for p in problems if p.code == "data_recovered"), None)
    detail: dict[str, str | int] = dict(earlier.detail) if earlier else {}
    detail |= {name: 1 for name in used.restored}
    problems[:] = [p for p in problems if p.code not in gone and p.code != "data_recovered"]
    problems.append(Problem(code="data_recovered", detail=detail))
    await announce(request.app.state.event_bus)


async def _audit(request: Request, capability: str, detail: str) -> None:
    """The person's action, in her activity log when her memory is open."""
    kernel = getattr(request.app.state, "kernel_runtime", None)
    if kernel is None:
        return
    actor, via = _who(request)
    await kernel.audit.record(
        capability=capability,
        actor=actor,
        decision=Decision.confirmed,
        result=CapabilityResult(ok=True, detail=detail),
        args={},
        duration_ms=0,
        confirm_via=via,
    )


def _used_words(used: Used) -> str:
    if used.restored:
        return f"A recovery key opened {' and '.join(used.restored)} for this Windows account."
    return "A recovery key was checked, and fits."
