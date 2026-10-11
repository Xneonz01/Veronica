"""A surface's tools: the capabilities it offers the model, run by the kernel.

Every tool is a capability, memory included, so every call the model makes goes
through policy, confirmation and the audit log. Text chat and the live voice call each
create a broker, differing only in `actor` and in which capabilities are exposed
(`voice_only` limits the call to voice-exposed ones).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.kernel.capability import CapabilityRequest, RiskTier
from app.kernel.confirm import PendingView
from app.kernel.kernel import CapabilityKernel
from app.kernel.registry import Capability, CapabilityRegistry
from app.kernel.taint import Taint, declared
from app.services.tools.render import render_result

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What a tool call produced: text for the model, plus status for the UI."""

    text: str
    ok: bool
    code: str = ""
    capability: str | None = None
    # What a person is shown: the capability's own account ("Read notes.md (1204
    # bytes)."), or what a recall found. Its `text` is written for the model: content
    # fenced with instructions to treat it as data, which on screen reads as her
    # talking to herself.
    summary: str = ""
    # The call's checked arguments, when it ran: the model's own words from here on,
    # so an unchanged retry isn't taken for a copy of the result that quoted them.
    cleared: tuple[str, ...] = ()

    @property
    def shown(self) -> str:
        """What a surface shows and a transcript keeps."""
        return self.summary or self.text


class ToolBroker:
    """Exposes the kernel's capabilities as one surface's tools."""

    def __init__(
        self,
        kernel: CapabilityKernel,
        registry: CapabilityRegistry,
        *,
        actor: str,
        voice_only: bool = False,
    ) -> None:
        self._kernel = kernel
        self._registry = registry
        self._actor = actor
        self._voice_only = voice_only
        self._schemas: list[dict[str, Any]] | None = None

    @property
    def actor(self) -> str:
        """Who this broker acts as ("veronica:voice", "veronica:chat"), on every request
        and so on every prompt it raises."""
        return self._actor

    def schemas(self) -> list[dict[str, Any]]:
        """The tool list, built once per session."""
        if self._schemas is None:
            offered = self._registry.offered(call=self._voice_only)
            self._schemas = self._registry.schemas(offered)
        return self._schemas

    def will_confirm(
        self, tool_name: str, arguments: dict[str, Any], taint: Taint | None = None
    ) -> bool:
        """Whether this call will wait for the user to confirm it: its policy asks, or
        an argument came from what she read (`taint`, the turn's).

        Callers use it to show that on the surface: a confirmation can hold the turn
        until it times out, and a UI still showing "thinking" that long looks hung. A
        call has to speak the question while the kernel waits. Advisory only; the
        kernel re-reads policy and makes the actual decision.
        """
        capability = self._registry.by_tool_name(tool_name)
        if capability is None:
            return False
        return self._kernel.requires_confirmation(self._request(capability, arguments, taint))

    def pending_confirmations(self) -> list[PendingView]:
        """Prompts currently awaiting an answer, so a surface can render or speak them."""
        return self._kernel.pending_confirmations()

    async def prompt_for(
        self, request_id: str, running: asyncio.Future[Any], timeout: float = 2.0
    ) -> PendingView | None:
        """The prompt the request `request_id` raised (`execute`), once the kernel mints it.

        Polls rather than subscribes: the prompt is also published on the bus, but it
        appears milliseconds after `execute` starts, and a subscription would need
        tearing down on every path out of the turn, including cancellation. It stops as
        soon as the call is over: refused before any prompt (bad arguments, a denied
        policy, nothing to forget), a call used to wait its full two seconds in silence
        and chat flashed "waiting for your OK" with no prompt to answer.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline and not running.done():
            for view in self.pending_confirmations():
                # Its own request's prompt only, by the id it carried: a chat, or another
                # call, asking about the same action at the same moment has one too, and
                # a "yes" said on this call must not answer it (bug 36: two calls both
                # act as "veronica:voice", and matching by actor and tool took either).
                if view.request_id == request_id:
                    return view
            await asyncio.sleep(0.02)
        return None

    def resolve_confirmation(self, token: str, approved: bool, *, via: str) -> bool:
        """Answer a pending confirmation from this surface.

        A spoken yes (`voice/realtime/spoken_confirm.py`) resolves the same token as the
        on-screen card, and `via` records which channel answered. Tokens are single-use,
        so if screen and voice race, the second answer is a no-op.
        """
        return self._kernel.resolve_confirmation(token, approved, via=via)

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        taint: Taint | None = None,
        budget: int | None = None,
        request_id: str | None = None,
    ) -> ToolOutcome:
        """Run one call through the kernel. `budget` is the tokens its result may take
        of the prompt (`render_result`); `request_id` is how `prompt_for` finds the
        prompt it raises."""
        capability = self._registry.by_tool_name(tool_name)
        if capability is None:
            logger.info("model asked for an unknown tool: %r", tool_name)
            return ToolOutcome(text="That tool isn't available.", ok=False, code="unknown_tool")

        request = self._request(capability, arguments, taint, request_id)
        result = await self._kernel.execute(request)
        shown = result.data.get("shown")
        return ToolOutcome(
            text=render_result(capability.name, result, for_voice=self._voice_only, budget=budget),
            ok=result.ok,
            code=result.code,
            capability=capability.name,
            summary=str(shown or result.detail or ("Done." if result.ok else "That didn't work.")),
            cleared=(
                declared(arguments, capability.args_model, skip=capability.selectors)
                if result.ok and taint is not None and capability.risk is not RiskTier.safe
                else ()
            ),
        )

    def _request(
        self,
        capability: Capability,
        arguments: dict[str, Any],
        taint: Taint | None,
        request_id: str | None = None,
    ) -> CapabilityRequest:
        return CapabilityRequest(
            name=capability.name,
            args=arguments,
            actor=self._actor,
            taint=taint,
            request_id=request_id,
        )


def after_round(taint: Taint | None, outcomes: Iterable[ToolOutcome]) -> Taint | None:
    """What the next round's calls are checked against (`kernel/taint.py`).

    This round's results join what she read, so a result can make only a call the
    model wrote after reading it ask. And each call that ran lends its arguments to a
    retry of the same capability, never to another one: a yes to a file write is not a
    yes to saving its text to memory.
    """
    if taint is None:
        return None
    done = list(outcomes)
    taint = taint.adding("tool_result", [outcome.text for outcome in done])
    for outcome in done:
        if outcome.capability is not None and outcome.cleared:
            taint = taint.keeping(outcome.capability, outcome.cleared)
    return taint
