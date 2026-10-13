"""What the kernel writes down about each request, for each way one can end.

The audit row, the perf row, the black box and the bus event. Kept apart from the
pipeline (`kernel.py`) so the order of its decisions reads in one place and the
bookkeeping they all share in another.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from app.core.event_bus import EventBus
from app.kernel.capability import AuditSink, CapabilityResult, Decision
from app.kernel.errors import Code
from app.kernel.measure import measure, ms_since
from app.kernel.registry import Capability, CapabilityRegistry
from app.kernel.verdict import AFTER_WEB, FROM_READING, Verdict
from app.schemas.events import Event
from app.telemetry import current_blackbox


class Records:
    """The kernel's record of every request: audited, measured and announced."""

    def __init__(self, registry: CapabilityRegistry, audit: AuditSink, bus: EventBus) -> None:
        self._registry = registry
        self._audit = audit
        self._bus = bus

    async def emit(self, event_type: str, payload: dict[str, object]) -> None:
        await self._bus.publish(Event(type=event_type, source="kernel", payload=payload))

    async def completed(
        self,
        capability: Capability,
        args: BaseModel,
        actor: str,
        decision: Decision,
        result: CapabilityResult,
        started: float,
        confirm_via: str | None,
    ) -> CapabilityResult:
        """A capability that ran, whatever its handler answered."""
        # Shielded, here and in `refused`: a cancellation that lands during the write
        # rolled the row back, and an action that had run left no record at all.
        await asyncio.shield(
            self._audit.record(
                capability=capability.name,
                actor=actor,
                decision=decision if result.ok else Decision.failed,
                result=result,
                args=args.model_dump(),
                duration_ms=ms_since(started),
                confirm_via=confirm_via,
            )
        )
        measure(capability.name, decision, ok=result.ok, started=started)
        await self.emit(
            "capability.completed",
            {
                "capability": capability.name,
                "ok": result.ok,
                "code": result.code,
                "duration_ms": ms_since(started),
            },
        )
        return result

    async def refused(
        self,
        name: str,
        args: dict[str, object],
        actor: str,
        code: Code,
        detail: str,
        decision: Decision,
        started: float,
        *,
        confirm_via: str | None = None,
    ) -> CapabilityResult:
        """A request the kernel ended without running it, or whose run failed."""
        result = CapabilityResult(ok=False, code=code.value, detail=detail)
        # An unregistered name came from the model and could be any text, so it is
        # recorded as "unknown" everywhere. Telemetry gets names and codes only;
        # arguments go to the audit log alone, redacted.
        known = name if self._registry.get(name) is not None else "unknown"
        current_blackbox().record(
            "warn", "capability.refused", capability=known, code=code.value, decision=decision.value
        )
        measure(known, decision, ok=False, started=started)
        await asyncio.shield(
            self._audit.record(
                capability=known,
                actor=actor,
                decision=decision,
                result=result,
                args=args,
                duration_ms=ms_since(started),
                confirm_via=confirm_via,
            )
        )
        await self.emit(
            "capability.denied", {"capability": known, "code": code.value, "reason": detail}
        )
        return result

    def cancelled(
        self,
        name: str,
        args: BaseModel,
        actor: str,
        started: float,
        confirm_via: str | None,
        detail: str,
    ) -> None:
        """A request cut off by a barge-in or a hang-up. Recorded without awaiting, so
        the cancelled task doesn't wait on the audit write lock."""
        self._audit.record_detached(
            capability=name,
            actor=actor,
            decision=Decision.cancelled,
            result=CapabilityResult(ok=False, code=Code.capability_failed, detail=detail),
            args=args.model_dump(),
            duration_ms=ms_since(started),
            confirm_via=confirm_via,
        )


def note(capability: Capability, verdict: Verdict) -> None:
    """The black box's line for a request that asked because of what it read or would
    send: the field, its source and its length, or the gate's reason, never the text or
    an address, which may be the attacker's own (or the data being smuggled out)."""
    if verdict.tainted is not None:
        current_blackbox().record(
            "info",
            "capability.from_reading",
            capability=capability.name,
            **verdict.tainted.as_fields(),
        )
    elif verdict.why is not None and verdict.why not in (FROM_READING, AFTER_WEB):
        current_blackbox().record(
            "info", "capability.egress", capability=capability.name, why=verdict.why
        )
