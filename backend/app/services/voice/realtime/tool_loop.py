"""The voice tool loop: run tools mid-call without going quiet or losing the floor.

Follows `chat/replies.py::stream_reply` in behaviour (bounded rounds, with a final
round forced tool-less so a turn always ends in speech), but the shape differs
because TTS streams. Two measurements shaped it:

* Attaching the voice tool list costs no latency: qwen3:8b measured 391 ms
  time-to-first-token with it against 406 ms without, and picked the right tool
  3 of 3 times. So the tools are always attached; there is no router and no switch
  to a bigger model.
* A tool round usually emits no prose, so the spoken acknowledgement below is
  needed: without it the user hears silence while the tool runs.

Prose that does arrive alongside a tool call is spoken rather than buffered. It
works as an acknowledgement and the next round continues the same answer, while
buffering it would cost first-audio latency. The chunker holds a sentence until text
arrives after it, and the last sentence of a model call has none, so `speak_held`
runs as each call ends and after the filler: otherwise "Let me check." would wait
for the tool it announces to finish.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.kernel.taint import Taint
from app.services.tools import ToolBroker, ToolOutcome, after_round, went_down
from app.services.tools.markup import LAST_ROUND, MarkupGate
from app.services.tools.sources import read_sources
from app.services.voice.realtime import spoken_confirm
from app.services.voice.realtime.metrics import TurnMetrics
from app.services.voice.realtime.reply_cap import ReplyBudget, read_reply

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3

# How long a tool may run before the call says something. Short enough that the pause
# never sounds like a fault, long enough that a fast local read needs no filler.
ACK_AFTER_S = 0.7

# Varied so a session doesn't sound repetitive. Never a tool name, path or code: the
# user asked in plain language and gets plain language back.
_ACKS = [
    "One moment.",
    "Let me look.",
    "Give me a second.",
    "Checking now.",
    "Just a moment.",
]


def acknowledgement(now: datetime | None = None) -> str:
    """A short spoken filler, rotated by the clock (the `greeting.py` pattern)."""
    moment = now or datetime.now()
    return _ACKS[(moment.hour * 60 + moment.minute + moment.second) % len(_ACKS)]


async def run_tool_rounds(
    s: RealtimeSession,
    broker: ToolBroker,
    messages: list[dict[str, Any]],
    *,
    model: str,
    metrics: TurnMetrics,
    speak: Callable[[str], Awaitable[None]],
    speak_held: Callable[[], Awaitable[None]],
    budget: ReplyBudget,
    taint: Taint | None = None,
) -> list[ToolOutcome]:
    """Stream a reply, running tool rounds until the model stops asking for tools, and
    return what the calls produced, in order.

    `speak(text)` is awaited for every chunk of content, and `speak_held()` when a
    model call ends. They are the chunker and TTS path from `workers`, so ordering,
    metrics and barge-in stay in one place.

    `budget` is the turn's allowance, shared by every model call (`reply_cap.py`). A
    call ended at the ceiling has no terminal chunk and so no tool calls, which ends
    the turn.

    `taint` is what the turn had read and what the user said (`kernel/taint.py`); each
    round's results join what was read before the next round, and a request heard while
    a call waited on an answer joins what the user said.

    Cancellation propagates untouched: barge-in cancels the task running this, and the
    kernel lets `CancelledError` through, so an in-flight capability is cancelled with
    the turn instead of finishing after the call has moved on.
    """
    ran: list[ToolOutcome] = []
    down: set[str] = set()
    for round_index in range(MAX_TOOL_ROUNDS):
        last = round_index == MAX_TOOL_ROUNDS - 1
        tools = None if last else broker.schemas(without=down)
        # Asked without tools, a model may still write a call out as text: never read
        # aloud (`tools/markup.py`).
        gate = MarkupGate() if last else None
        spoke_this_round = False
        tool_calls: list[dict[str, Any]] | None = None

        asked = [*messages, LAST_ROUND] if last else messages
        calling = s.runtime.llm.stream_chat(asked, tools=tools, model=model)
        async with read_reply(calling, budget) as stream:
            async for chunk in stream:
                said = gate.feed(chunk.content) if gate is not None else chunk.content
                if said:
                    metrics.mark_ttft()
                    await speak(said)
                    spoke_this_round = True
                if chunk.done:
                    tool_calls = chunk.tool_calls
        if gate is not None and (rest := gate.end()):
            metrics.mark_ttft()
            await speak(rest)
        await speak_held()

        if last or not tool_calls:
            return ran

        metrics.tool_calls += len(tool_calls)
        messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
        outcomes: list[ToolOutcome] = []
        heard: list[str] = []
        for call in tool_calls:
            filled, outcome, carried = await _run_one(
                s,
                broker,
                call,
                messages,
                speak=speak,
                speak_held=speak_held,
                spoke=spoke_this_round,
                taint=taint,
            )
            outcomes.append(outcome)
            if carried:
                heard.append(carried)
            # Once a round: two slow tools in a row said "One moment." twice.
            spoke_this_round = spoke_this_round or filled
        ran += outcomes
        down |= went_down(outcomes)
        note_sources(s, outcomes)
        taint = after_round(taint, outcomes)
        if taint is not None:
            taint = taint.trusting(heard)
    return ran


def note_sources(s: RealtimeSession, outcomes: list[ToolOutcome]) -> None:
    """Keep what these calls may have put in the reply, and the addresses a search
    returned, as they finish: a reply that fails or is cut off later is marked too."""
    s.turn_sources |= read_sources(outcome.capability for outcome in outcomes)
    s.urls = s.urls.union(url for outcome in outcomes for url in outcome.urls)


def carry(s: RealtimeSession, entry: dict[str, Any]) -> None:
    """Mark a reply in history with every source its turn's tools may have put in it."""
    for source in sorted(s.turn_sources):
        s.carried.append((source, entry))


async def _run_one(
    s: RealtimeSession,
    broker: ToolBroker,
    call: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    speak: Callable[[str], Awaitable[None]],
    speak_held: Callable[[], Awaitable[None]],
    spoke: bool,
    taint: Taint | None,
) -> tuple[bool, ToolOutcome, str | None]:
    """Execute one tool call, filling the silence if it takes a noticeable moment.

    Returns whether it said the filler, what the call produced, and a request the user
    made instead of answering its question, if they did.
    """
    from app.services.chat.replies import tool_args  # shared parser; models vary

    function = call.get("function", {})
    name = str(function.get("name", ""))
    # Check before executing: the kernel will park on its confirmation window (45 s by
    # default), and the question has to be spoken while it waits or it expires unheard.
    args = tool_args(function)
    asking = broker.will_confirm(name, args, taint)
    request_id = secrets.token_hex(8)  # how its prompt is told from another call's
    task = asyncio.ensure_future(broker.execute(name, args, taint=taint, request_id=request_id))
    carried: str | None = None
    filled = False
    try:
        if asking:
            carried = await spoken_confirm.ask(s, request_id, task)
            outcome = await task
        else:
            # Use `asyncio.wait`, not `wait_for(shield(...))`: the shield would absorb
            # the barge-in's cancellation and let the capability finish after the user
            # interrupted. `wait` doesn't shield, and the handler below cancels the task.
            done, _ = await asyncio.wait({task}, timeout=ACK_AFTER_S)
            if not done and not spoke:
                # Still running and nothing was said this round, so the user is
                # hearing silence: fill it, now rather than with the next round's text.
                filled = True
                await speak(acknowledgement())
                await speak_held()
            outcome = await task
    except BaseException:
        # Barge-in, shutdown, or the filler or the question failing to be said. The
        # capability belongs to a turn that is over, so cancel it before its side effect
        # lands (a write approved on screen after the turn failed), then let it through.
        task.cancel()
        raise
    finally:
        s.awaiting_confirm = None  # the window is over however it ended
    messages.append({"role": "tool", "content": outcome.text})
    _log_outcome(name, outcome)
    # A reply to the confirmation that was really a new request, now that the
    # confirmation is closed: into the call's history, and into this turn's messages,
    # so the next round answers it. It used to wait for the turn after.
    if carried:
        messages.append({"role": "user", "content": carried})
        s.history.append({"role": "user", "content": carried})
    return filled, outcome, carried


def _log_outcome(name: str, outcome: ToolOutcome) -> None:
    if outcome.ok:
        logger.info("voice tool %s ok", name)
    else:
        logger.info("voice tool %s refused: %s", name, outcome.code)
