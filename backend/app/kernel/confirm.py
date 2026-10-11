"""The confirmation gate: a guarded action waits for a human yes.

It fails closed: a timeout, an unknown or already-used token, shutdown, or a
failure to publish the prompt all resolve to "denied". The absence of an answer
never means yes. A prompt that couldn't be published is marked `unasked`, so it isn't
said as the person's no.

`resolve(token, approved, *, via)` is surface-agnostic: the UI answers over REST
(`via="ui"`) and a spoken answer during a call arrives with `via="voice"`. `via` is
recorded in the audit row.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.core.event_bus import EventBus
from app.kernel.registry import Capability
from app.schemas.events import Event

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 45.0

# Redacts an args dict before it is shown in a prompt (the audit store supplies it).
RedactFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ConfirmOutcome:
    approved: bool
    timed_out: bool = False
    via: str | None = None
    # The prompt couldn't be published: nobody was asked, so nobody said no either.
    unasked: bool = False


@dataclass(slots=True)
class _Pending:
    token: str
    capability: str
    summary: str
    risk: str
    actor: str
    expires_at: float
    future: asyncio.Future[tuple[bool, str]]
    why: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PendingView:
    """What a surface needs to render a prompt (never the raw args)."""

    token: str
    capability: str
    summary: str
    risk: str
    actor: str
    expires_in_ms: int
    # Why it asks when its policy wouldn't have: `kernel.FROM_READING`, or None.
    why: str | None = None
    # The id its request carried (`CapabilityRequest.request_id`), for the surface
    # that asked to find its own prompt by.
    request_id: str | None = None


class ConfirmationBroker:
    """Holds pending confirmations and resolves them from any surface."""

    def __init__(
        self,
        bus: EventBus,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        redact: RedactFn | None = None,
    ) -> None:
        self._bus = bus
        self._timeout_s = timeout_s
        self._redact = redact
        self._pending: dict[str, _Pending] = {}
        self._resolved_tasks: set[asyncio.Task[None]] = set()

    async def request(
        self,
        capability: Capability,
        args: BaseModel,
        actor: str,
        *,
        summary: str | None = None,
        why: str | None = None,
        request_id: str | None = None,
    ) -> ConfirmOutcome:
        """Ask, and wait for the answer. `summary` is what the prompt names, when the
        kernel knows better than the arguments do (a capability's preflight); `why` is
        set when the prompt was raised for something its policy would have let run."""
        token = secrets.token_urlsafe(16)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[bool, str]] = loop.create_future()
        summary = summary or capability.summarize(args)
        pending = _Pending(
            token=token,
            capability=capability.name,
            summary=summary,
            risk=capability.risk.value,
            actor=actor,
            expires_at=time.monotonic() + self._timeout_s,
            future=future,
            why=why,
            request_id=request_id,
        )
        self._pending[token] = pending

        preview = args.model_dump()
        if self._redact is not None:
            preview = self._redact(preview)
        # One `finally` covers everything after the insert. Publishing suspends while
        # subscribers run, so a cancellation can land there, and `except Exception`
        # won't catch it (CancelledError is a BaseException). A narrower scope would
        # leave a stale pending prompt that never clears.
        try:
            try:
                await self._bus.publish(
                    Event(
                        type="capability.confirm_required",
                        source="kernel",
                        payload={
                            "token": token,
                            "capability": capability.name,
                            "summary": summary,
                            "risk": capability.risk.value,
                            "actor": actor,
                            "args_preview": preview,
                            "expires_in_ms": int(self._timeout_s * 1000),
                            "why": why,
                        },
                    )
                )
            except Exception:
                # If nobody can be asked, nobody approved it. Fail closed immediately.
                logger.exception("could not publish a confirmation prompt; denying")
                return ConfirmOutcome(approved=False, unasked=True)

            approved, via = await asyncio.wait_for(future, self._timeout_s)
            return ConfirmOutcome(approved=approved, via=via)
        except TimeoutError:
            await self._expire(token, "timeout")
            return ConfirmOutcome(approved=False, timed_out=True)
        except asyncio.CancelledError:
            await self._expire(token, "cancelled")
            raise
        finally:
            self._pending.pop(token, None)

    def resolve(self, token: str, approved: bool, *, via: str = "ui") -> bool:
        """Answer a pending confirmation. Single-use: a replay returns False.

        The `capability.confirm_resolved` event lets every surface clear the same
        prompt, whether it was answered on screen or by voice.
        """
        pending = self._pending.pop(token, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result((approved, via))
        self._announce(token, approved, via)
        return True

    def _announce(self, token: str, approved: bool, via: str) -> None:
        """Fire-and-forget: `resolve` is sync and called from request handlers."""
        task = asyncio.create_task(
            self._bus.publish(
                Event(
                    type="capability.confirm_resolved",
                    source="kernel",
                    payload={"token": token, "approved": approved, "via": via},
                )
            )
        )
        self._resolved_tasks.add(task)
        task.add_done_callback(self._resolved_tasks.discard)

    def pending(self) -> list[PendingView]:
        """Outstanding prompts, so a reloaded surface can re-render them."""
        now = time.monotonic()
        return [
            PendingView(
                token=item.token,
                capability=item.capability,
                summary=item.summary,
                risk=item.risk,
                actor=item.actor,
                expires_in_ms=max(0, int((item.expires_at - now) * 1000)),
                why=item.why,
                request_id=item.request_id,
            )
            for item in self._pending.values()
        ]

    async def aclose(self) -> None:
        """Shutdown denies everything outstanding rather than leaving it hanging."""
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_result((False, "shutdown"))
        self._pending.clear()

    async def _expire(self, token: str, reason: str) -> None:
        self._pending.pop(token, None)
        await self._bus.publish(
            Event(
                type="capability.confirm_expired",
                source="kernel",
                payload={"token": token, "reason": reason},
            )
        )
