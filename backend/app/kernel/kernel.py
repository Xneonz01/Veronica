"""The pipeline: resolve -> validate -> verdict -> [preflight, confirm] -> run -> audit.

What to do with a request is decided once, in `verdict.py`, from the capability, its
policy, the turn's budget and what the turn read. This module only carries a verdict
out. What each ending writes down is `records.py`, and the capability's own check before
anyone is asked runs through `preflight.py`.

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
from app.core.logging import log_failure
from app.kernel.budget import Budget
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
from app.kernel.records import Records, note
from app.kernel.registry import Capability, CapabilityRegistry
from app.kernel.verdict import AFTER_WEB, FROM_READING, Action, Verdict, decide

logger = logging.getLogger(__name__)
_SPENT = "I've done that as many times as I can for now, so I stopped there."


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
        budget: Budget | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._confirm = confirmations
        self._records = Records(registry, audit, bus)
        self._taint_min = taint_min_words
        self._budget = budget

    def pending_confirmations(self) -> list[PendingView]:
        """Outstanding prompts (any surface may render or speak them)."""
        return self._confirm.pending()

    def resolve_confirmation(self, token: str, approved: bool, *, via: str) -> bool:
        """Answer a pending confirmation from any surface (UI, voice, ...)."""
        return self._confirm.resolve(token, approved, via=via)

    def requires_confirmation(self, req: CapabilityRequest) -> bool:
        """Whether this request would currently park on a human answer. A call has to
        know before the kernel waits, so the question can be spoken while it does; the
        answer is the verdict `execute` acts on, never a second opinion."""
        found = self.verdict(req)
        return found is not None and found.action is Action.ask

    def verdict(self, req: CapabilityRequest) -> Verdict | None:
        """The verdict `execute` would act on now, and why; None for a request refused
        before any verdict (a capability it lacks, arguments it can't read)."""
        capability = self._registry.get(req.name)
        if capability is None:
            return None
        try:
            args = capability.args_model.model_validate(req.args)
        except ValidationError:
            return None
        return self._decide(capability, args, req)

    def _decide(self, capability: Capability, args: BaseModel, req: CapabilityRequest) -> Verdict:
        mode = self._policy.mode_for(capability)
        return decide(
            capability, args, req, mode, budget=self._budget, taint_min_words=self._taint_min
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
        verdict = self._decide(capability, args, req)
        note(capability, verdict)
        if verdict.action is Action.refuse:
            code = verdict.code or Code.policy_denied
            return await refuse(
                req.name,
                args.model_dump(),
                req.actor,
                code,
                verdict.detail,
                Decision.denied,
                started,
            )
        confirm_via: str | None = None
        decision = Decision.allowed
        if verdict.action is Action.ask:
            outcome = await self._ask(capability, args, req, verdict, started)
            if isinstance(outcome, CapabilityResult):
                return outcome
            confirm_via, decision = outcome.via, Decision.confirmed
        taint = req.taint
        if self._budget is not None and not self._budget.take(
            capability.name, taint.turn if taint is not None else None
        ):
            return await refuse(
                req.name,
                args.model_dump(),
                req.actor,
                Code.budget_exceeded,
                _SPENT,
                Decision.denied,
                started,
                confirm_via=confirm_via,
            )
        return await self._run(capability, args, req.actor, decision, confirm_via, started)

    async def _ask(
        self,
        capability: Capability,
        args: BaseModel,
        req: CapabilityRequest,
        verdict: Verdict,
        started: float,
    ) -> ConfirmOutcome | CapabilityResult:
        """A person's answer, or the refusal that ends the request without one."""
        refuse = self._records.refused
        asked: str | None = None  # what the prompt names, when the preflight says
        if capability.preflight is not None:
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
        try:
            outcome = await self._confirm.request(
                capability,
                args,
                req.actor,
                summary=asked,
                why=verdict.why,
                request_id=req.request_id,
            )
        except asyncio.CancelledError:
            # Cut off while waiting for an answer. It was asked, so it is on the record
            # like every request; this path used to leave no row at all.
            self._records.cancelled(
                req.name, args, req.actor, started, None, "Cancelled before an answer."
            )
            raise
        if outcome.approved:
            return outcome
        code, detail = outcome.refusal()
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
            # The type and where it was raised, never the message: a message can quote
            # an argument, a page or a key (an encoding error quotes the string).
            log_failure(logger, f"capability {capability.name} failed", exc)
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


__all__ = [
    "AFTER_WEB",
    "FROM_READING",
    "AuditSink",
    "CapabilityKernel",
    "ConfirmOutcome",
    "PolicyMode",
    "RiskTier",
]
