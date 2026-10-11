"""The pipeline: resolve -> validate -> policy -> taint -> [preflight, confirm] -> run -> audit.

Kept out of `capability.py` so that file stays a pure contract other modules import
without pulling the whole kernel in. What each ending writes down is `records.py`, and
the capability's own check before anyone is asked runs through `preflight.py`.

Two invariants:
  * `CancelledError` propagates. Barge-in cancels the reply task, which cancels the
    capability in flight; turning the cancellation into a result would break barge-in.
  * The audit write is awaited before the result is returned, so an action never
    reports success ahead of its own record.
"""

from __future__ import annotations

import asyncio
import logging
import time

from pydantic import BaseModel, ValidationError

from app.core.event_bus import EventBus
from app.kernel.capability import (
    AuditSink,
    CapabilityRequest,
    CapabilityResult,
    Decision,
    RiskTier,
)
from app.kernel.confirm import ConfirmationBroker, ConfirmOutcome, PendingView
from app.kernel.errors import Code, safe_validation_detail
from app.kernel.policy import PolicyMode, PolicyStore
from app.kernel.preflight import as_code, check
from app.kernel.records import Records
from app.kernel.registry import Capability, CapabilityRegistry
from app.kernel.taint import Tainted, find
from app.telemetry import current_blackbox

logger = logging.getLogger(__name__)

# Why a prompt was raised for a call its policy would have let run: an argument came
# word for word from what she read. Surfaces say so on the prompt. Only then: a call
# that asks anyway, a file write to a path a search found, needs no explaining.
FROM_READING = "from_reading"


class CapabilityKernel:
    """Executes capability requests after policy and confirmation checks."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        policy: PolicyStore,
        confirmations: ConfirmationBroker,
        audit: AuditSink,
        bus: EventBus,
        *,
        taint_min_words: int = 2,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._confirm = confirmations
        self._records = Records(registry, audit, bus)
        self._taint_min = taint_min_words

    def pending_confirmations(self) -> list[PendingView]:
        """Outstanding prompts (any surface may render or speak them)."""
        return self._confirm.pending()

    def resolve_confirmation(self, token: str, approved: bool, *, via: str) -> bool:
        """Answer a pending confirmation from any surface (UI, voice, ...)."""
        return self._confirm.resolve(token, approved, via=via)

    def requires_confirmation(self, req: CapabilityRequest) -> bool:
        """Whether this request would currently park on a human answer: its policy
        asks, or an argument came from what she read. A call has to know before the
        kernel waits, so the question can be spoken while it does."""
        capability = self._registry.get(req.name)
        if capability is None:
            return False
        mode = self._policy.mode_for(capability)
        return mode is PolicyMode.confirm or (
            mode is PolicyMode.auto_allow and self._provenance(capability, req) is not None
        )

    def _provenance(self, capability: Capability, req: CapabilityRequest) -> Tainted | None:
        """The argument that came word for word from what she read, if one did.

        Not for a safe capability: a read changes nothing, and checking one would ask
        about every file opened from a search result.
        """
        if req.taint is None or capability.risk is RiskTier.safe:
            return None
        return find(
            req.args,
            capability.args_model,
            req.taint,
            capability=capability.name,
            min_words=self._taint_min,
            selectors=capability.selectors,
        )

    async def execute(self, req: CapabilityRequest) -> CapabilityResult:
        started = time.perf_counter()
        refuse = self._records.refused
        capability = self._registry.get(req.name)
        if capability is None:
            return await refuse(
                req.name,
                req.args,
                req.actor,
                Code.unknown_capability,
                "I don't have that ability.",
                Decision.failed,
                started,
            )

        try:
            args = capability.args_model.model_validate(req.args)
        except ValidationError as exc:
            return await refuse(
                req.name,
                req.args,
                req.actor,
                Code.invalid_args,
                safe_validation_detail(exc),
                Decision.failed,
                started,
            )

        await self._records.emit(
            "capability.requested", {"capability": req.name, "actor": req.actor}
        )

        mode = self._policy.mode_for(capability)
        if mode is PolicyMode.deny:
            return await refuse(
                req.name,
                args.model_dump(),
                req.actor,
                Code.policy_denied,
                "That action is switched off.",
                Decision.denied,
                started,
            )
        tainted = self._provenance(capability, req)
        why: str | None = None
        if tainted is not None:
            # The field, its source and its length, never the text: it may be the
            # attacker's own sentence.
            current_blackbox().record(
                "info", "capability.from_reading", capability=req.name, **tainted.as_fields()
            )
            if mode is PolicyMode.auto_allow:
                mode = PolicyMode.confirm  # whatever the tier: a person decides
                why = FROM_READING
        asked: str | None = None  # what the prompt names, when the preflight says
        if mode is PolicyMode.confirm and capability.preflight is not None:
            checked = await check(
                capability,
                capability.preflight,
                args,
                self._records,
                actor=req.actor,
                started=started,
            )
            if not checked.ok:  # nothing to ask about: it couldn't go ahead
                return await refuse(
                    req.name,
                    args.model_dump(),
                    req.actor,
                    as_code(checked.code),
                    checked.detail,
                    Decision.failed,
                    started,
                )
            asked = checked.detail or None

        confirm_via: str | None = None
        decision = Decision.allowed
        if mode is PolicyMode.confirm:
            try:
                outcome = await self._confirm.request(
                    capability,
                    args,
                    req.actor,
                    summary=asked,
                    why=why,
                    request_id=req.request_id,
                )
            except asyncio.CancelledError:
                # Cut off while waiting for an answer. It was asked, so it is on the
                # record like every request; this path used to leave no row at all.
                self._records.cancelled(
                    req.name, args, req.actor, started, None, "Cancelled before an answer."
                )
                raise
            if not outcome.approved:
                code, detail = _not_approved(outcome)
                return await refuse(
                    req.name,
                    args.model_dump(),
                    req.actor,
                    code,
                    detail,
                    # Nobody was asked: the gate failed, no person said no.
                    Decision.failed if outcome.unasked else Decision.denied,
                    started,
                    confirm_via=outcome.via,
                )
            confirm_via = outcome.via
            decision = Decision.confirmed

        return await self._run(capability, args, req.actor, decision, confirm_via, started)

    async def _run(
        self,
        capability: Capability,
        args: BaseModel,
        actor: str,
        decision: Decision,
        confirm_via: str | None,
        started: float,
    ) -> CapabilityResult:
        try:
            result = await asyncio.wait_for(capability.handler(args), capability.timeout_s)
        except TimeoutError:
            return await self._records.refused(
                capability.name,
                args.model_dump(),
                actor,
                Code.capability_timeout,
                "That took too long, so I stopped it.",
                Decision.failed,
                started,
                confirm_via=confirm_via,
            )
        except asyncio.CancelledError:
            # A handler already running in a thread (a file write, an app launch) can't
            # be stopped by the cancellation, so the record can't say it didn't happen.
            self._records.cancelled(
                capability.name,
                args,
                actor,
                started,
                confirm_via,
                "Cancelled while it ran; it may have finished.",
            )
            raise
        except Exception as exc:  # a provider bug must never reach the user raw
            logger.exception("capability %s failed", capability.name)
            logger.debug("capability failure detail: %r", exc)
            return await self._records.refused(
                capability.name,
                args.model_dump(),
                actor,
                Code.capability_failed,
                "That didn't work.",
                Decision.failed,
                started,
                confirm_via=confirm_via,
            )
        return await self._records.completed(
            capability, args, actor, decision, result, started, confirm_via
        )


def _not_approved(outcome: ConfirmOutcome) -> tuple[Code, str]:
    """The code and the words for a confirmation that didn't end in a yes. A prompt that
    couldn't be published was said as "You declined that", though nobody was asked."""
    if outcome.unasked:
        return Code.confirm_unavailable, "I couldn't ask you about that, so I didn't do it."
    if outcome.timed_out:
        return Code.confirm_timeout, "That timed out waiting for your approval, so I didn't do it."
    return Code.confirm_denied, "You declined that, so I didn't do it."


__all__ = [
    "AuditSink",
    "CapabilityKernel",
    "ConfirmOutcome",
    "PolicyMode",
    "RiskTier",
]
