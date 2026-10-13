"""Capabilities REST: what Veronica can do, how each is approved, and approving it.

`GET /capabilities/pending` lets a surface that reloaded (or connected late)
re-render an outstanding prompt. Without it, a refresh mid-confirmation hides the
request until it times out. A prompt carries each argument clipped; the whole of one
is fetched from `GET /capabilities/pending/{token}` by the surface that shows it, so a
long file body is never sent to every window.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.refusals import Refusal
from app.kernel.policy import PolicyMode, PolicyViolation
from app.kernel.runtime import KernelRuntime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def _kernel(request: Request) -> KernelRuntime:
    runtime = getattr(request.app.state, "kernel_runtime", None)
    if runtime is None:  # started without her memory (`app/stores.py`)
        raise HTTPException(status_code=503, detail="Capabilities are unavailable.")
    kernel: KernelRuntime = runtime
    return kernel


class PolicyUpdate(BaseModel):
    mode: PolicyMode


class ConfirmDecision(BaseModel):
    token: str
    approved: bool


@router.get("")
async def list_capabilities(request: Request) -> dict[str, Any]:
    runtime = _kernel(request)
    return {
        "capabilities": [
            {
                "name": cap.name,
                "description": cap.description,
                "risk": cap.risk.value,
                "mode": runtime.policy.mode_for(cap).value,
            }
            for cap in runtime.registry
        ]
    }


@router.put("/{name}/policy")
async def set_policy(name: str, update: PolicyUpdate, request: Request) -> dict[str, str]:
    runtime = _kernel(request)
    capability = runtime.registry.get(name)
    if capability is None:
        raise HTTPException(status_code=404, detail="I don't have that ability.")
    try:
        await runtime.policy.set_mode(capability, update.mode)
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"capability": name, "mode": update.mode.value}


@router.get("/pending")
async def pending_confirmations(request: Request) -> dict[str, Any]:
    """Outstanding prompts, so a reloaded surface can re-render them."""
    pending = _kernel(request).confirmations.pending()
    # `asdict`, not `__dict__`: PendingView is a slots dataclass with no instance dict.
    return {"pending": [asdict(item) for item in pending]}


@router.get("/pending/{token}")
async def pending_values(token: str, request: Request) -> dict[str, Any]:
    """Every argument of one outstanding prompt in full (Show all): hidden characters
    shown, nothing blanked but a field named for a secret (`kernel/display.py`)."""
    values = _kernel(request).confirmations.full(token)
    if values is None:
        raise Refusal(404, "confirm_gone", "That request was already answered or ran out.")
    return {"token": token, "values": values}


@router.post("/confirm")
async def resolve_confirmation(decision: ConfirmDecision, request: Request) -> dict[str, bool]:
    """Approve or deny a pending action. Unknown or already-answered tokens no-op."""
    resolved = _kernel(request).confirmations.resolve(decision.token, decision.approved, via="ui")
    return {"resolved": resolved}
