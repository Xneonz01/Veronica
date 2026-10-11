"""Capability kernel contract: Veronica requests, the kernel executes.

This module is only the contract: the types every other layer imports. The
pipeline that consumes them lives in `kernel.py`, so a capability provider can
depend on these shapes without pulling in the registry, policy or audit store.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.kernel.taint import Taint


class RiskTier(StrEnum):
    """How much damage a capability can do — this drives the default policy."""

    safe = "safe"  # read-only; auto-allowed by default
    guarded = "guarded"  # mutates local state; confirmed by default
    dangerous = "dangerous"  # destructive or arbitrary execution; never auto-allowed


class Decision(StrEnum):
    """What the kernel did with a request (recorded verbatim in the audit log)."""

    allowed = "allowed"  # policy auto-allowed it
    confirmed = "confirmed"  # the user approved it
    denied = "denied"  # policy, the user, or a timeout refused it
    failed = "failed"  # it ran (or tried to) and errored
    cancelled = "cancelled"  # the caller was cancelled mid-flight (e.g. barge-in)


class CapabilityRequest(BaseModel):
    """A request to perform a real-world capability, e.g. 'files.read'."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    actor: str  # which surface requested it, e.g. "veronica:chat" or "veronica:voice"
    # What the turn had read and what the user had said, when a surface knows: an
    # argument copied from the first makes the call ask (`taint.py`). The turn's text,
    # not the call's, so it is never part of the request's record.
    taint: Taint | None = Field(default=None, exclude=True)
    # Chosen by the surface, so it can find the prompt this request raises: found by
    # actor and tool, two calls asking about the same action at once took each
    # other's, and a yes said in one answered the other (bug 36). Not recorded.
    request_id: str | None = Field(default=None, exclude=True)


class CapabilityResult(BaseModel):
    """Outcome of a capability execution.

    `code` is machine-readable and stable; the frontend maps it to user-facing copy.
    It defaults to empty, which is what a success carries. `detail` is a sentence for
    people and is kept in the audit log. `data` never is: it holds what the model is
    handed (quoted, as data) and, under `shown`, what a screen shows in place of
    `detail` when that would put private text in the log.
    """

    ok: bool
    code: str = ""
    detail: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class AuditSink(Protocol):
    """The kernel only needs to append; the store owns everything else."""

    async def record(
        self,
        *,
        capability: str,
        actor: str,
        decision: Decision,
        result: CapabilityResult,
        args: dict[str, object],
        duration_ms: int,
        confirm_via: str | None = None,
    ) -> None: ...

    def record_detached(
        self,
        *,
        capability: str,
        actor: str,
        decision: Decision,
        result: CapabilityResult,
        args: dict[str, object],
        duration_ms: int,
        confirm_via: str | None = None,
    ) -> None: ...


class Kernel(Protocol):
    """Executes capability requests after policy checks."""

    async def execute(self, req: CapabilityRequest) -> CapabilityResult: ...


class NotImplementedKernel:
    """A kernel that refuses everything: the null object for code with no kernel.

    The real kernel is `kernel.CapabilityKernel`. This one is for contexts that must
    not act, such as test doubles. Refusing is always the safe answer, and it keeps
    "no kernel wired" visible rather than silently granting.
    """

    async def execute(self, req: CapabilityRequest) -> CapabilityResult:
        return CapabilityResult(ok=False, detail="I can't take actions here.")
