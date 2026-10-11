"""Streaming one reply, including however many tool rounds it takes.

`session.py` owns the turn lifecycle (history, persistence, cancellation). This owns
only the exchange with the model: stream, run whatever it asks for, stream again. It
takes callbacks rather than the session so it can be tested on its own. The voice call
has its own loop with the same round semantics (`voice/realtime/tool_loop.py`),
because spoken replies stream through TTS.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.kernel.taint import Taint
from app.services.tools import ToolBroker, ToolOutcome, after_round

if TYPE_CHECKING:
    from app.adapters.llm.ollama_client import OllamaLLMClient
    from app.services.chat.window import TurnWindow

MAX_TOOL_ROUNDS = 3

Send = Callable[[dict[str, Any]], Awaitable[None]]
OnTool = Callable[[str, ToolOutcome], Awaitable[None]]


def tool_args(fn: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments as a dict — models often return them as a JSON string."""
    raw = fn.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


async def stream_reply(
    llm: OllamaLLMClient,
    broker: ToolBroker,
    messages: list[dict[str, Any]],
    *,
    model: str,
    send: Send,
    on_tool: OnTool,
    taint: Taint | None = None,
    window: TurnWindow | None = None,
) -> str:
    """Stream a reply, running tool rounds until the model stops asking.

    `taint` is what the turn had read and what the user said (`kernel/taint.py`). Each
    round's results join what was read before the next round: a result can make only a
    call the model wrote after reading it ask first. `window` holds `messages` inside
    the model's window: each result is cut to its share of the room, the oldest of the
    conversation makes way for them before the next round, and when even the turn's
    own rounds can't be made to fit, she answers from what she has, without tools.
    """
    reply = ""
    for _ in range(MAX_TOOL_ROUNDS):
        parts: list[str] = []
        tool_calls: list[dict[str, Any]] | None = None
        async for chunk in llm.stream_chat(messages, tools=broker.schemas(), model=model):
            if chunk.content:
                parts.append(chunk.content)
                await send({"type": "token", "text": chunk.content})
            if chunk.done:
                tool_calls = chunk.tool_calls
                if window is not None and chunk.usage is not None:
                    window.observe(chunk.usage.prompt_tokens)
        reply = "".join(parts)
        if not tool_calls:
            return reply
        messages.append({"role": "assistant", "content": reply, "tool_calls": tool_calls})
        budget = window.result_budget(len(tool_calls)) if window is not None else None
        outcomes: list[ToolOutcome] = []
        for call in tool_calls:
            fn = call.get("function", {})
            name = str(fn.get("name", ""))
            outcome = await _execute(broker, name, tool_args(fn), taint, send, budget)
            outcomes.append(outcome)
            await send(
                {
                    "type": "tool",
                    "name": name,
                    "result": outcome.shown,
                    "ok": outcome.ok,
                    "code": outcome.code,
                }
            )
            await on_tool(name, outcome)
            messages.append({"role": "tool", "content": outcome.text})
        taint = after_round(taint, outcomes)
        if window is not None and not window.make_room():
            break  # the tool list is room it can't spare

    # Rounds exhausted with the model still asking for tools, or no room for another
    # round: the last round's results are in `messages`, and nothing has read them yet.
    # One round without tools puts them into words. A sentence the last round carried
    # before its calls was returned here once, and what it fetched went unsaid (bug 25).
    parts = []
    async for chunk in llm.stream_chat(messages, tools=None, model=model):
        if chunk.content:
            parts.append(chunk.content)
            await send({"type": "token", "text": chunk.content})
        if chunk.done and window is not None and chunk.usage is not None:
            window.observe(chunk.usage.prompt_tokens, with_tools=False)
    # A round that wrote nothing leaves the last round's sentence, not an empty answer.
    return "".join(parts) or reply


async def _execute(
    broker: ToolBroker,
    name: str,
    args: dict[str, Any],
    taint: Taint | None,
    send: Send,
    budget: int | None,
) -> ToolOutcome:
    """Run one call, and say so while it waits for the user.

    A guarded action blocks until the user answers or it times out. The surface is
    told, or it would sit on "thinking" for the whole wait and look hung; but only once
    the prompt exists, or a call refused before anyone was asked (nothing to forget)
    flashed "waiting for your OK" with nothing to answer. A call still running when the
    wait for its prompt gives up is taken to be asking.
    """
    request_id = secrets.token_hex(8)  # how its prompt is told from another's
    running = asyncio.ensure_future(
        broker.execute(name, args, taint=taint, budget=budget, request_id=request_id)
    )
    asked = False
    try:
        if broker.will_confirm(name, args, taint):
            prompt = await broker.prompt_for(request_id, running)
            asked = prompt is not None or not running.done()
            if asked:
                await send({"type": "state", "state": "awaiting_confirmation"})
        outcome = await running
    except BaseException:
        # Stop or a disconnect: the action belongs to a turn that is over.
        running.cancel()
        raise
    if asked:
        await send({"type": "state", "state": "thinking"})
    return outcome
