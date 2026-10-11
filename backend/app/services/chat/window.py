"""What of a chat turn fits the model's window, so the persona and the tool rules are
never the part that doesn't.

Ollama doesn't refuse a prompt larger than its window: it keeps the last half and drops
the rest, the system prompt first. Measured on qwen3.5:9b through Ollama 0.33.3: a
51,654-character message came back as a 4,098-token prompt, and a rule at the start of
the system prompt was no longer followed; at 34,454 characters (7,064 tokens) it was. A
reply that fills the window pushes the front out the same way as it grows, so every
prompt leaves room for the longest reply (`LlmSettings.reply_tokens`).

What goes in, in order, while it fits: the persona and the tools, always; the message;
what she recalled for it, memory and then documents; then the conversation, newest
first, opening on a question. A message that doesn't fit beside the persona alone is
sent back before anything is saved or searched. Before each tool round the prompt is
fitted again: the oldest of the conversation makes way for the round's results, and
each result is cut to its share of what is left (`render_result`'s budget).

Tokens are estimated (`adapters/llm/tokens.py`), never counted by the model.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, NamedTuple

from app.adapters.llm.tokens import estimate, fewest_tokens
from app.telemetry import current_blackbox

logger = logging.getLogger(__name__)

#: The least room a message may have. With less, the window holds nothing worth
#: sending: the settings leave it too small, which is a different refusal.
MIN_MESSAGE_TOKENS = 256
# The chat template's own tokens around each message: its role and the markers either
# side (five in qwen3.5's, a few more around a tool result).
_FRAMING = 8
# What the turn's own rounds become when they no longer fit: a call keeps its short
# arguments and the length of a long one (a file saved is written in its call whole),
# and a result before the last round, a note.
_LONG_ARGUMENT = 200
_EARLIER_RESULT = "(An earlier result, left out: there was no room left in this conversation.)"

Refusal = Literal["message_too_long", "context_too_small"]


def prompt_room(num_ctx: int, reply_tokens: int, tools: int) -> int:
    """What a turn's messages may take: the window, less the longest reply and the
    tool list sent beside them (`tool_tokens`)."""
    return num_ctx - reply_tokens - tools


def tool_tokens(tools: list[dict[str, Any]]) -> int:
    """What the tool list takes of the window."""
    return estimate(json.dumps(tools))


def message_room(persona: str, room: int) -> int:
    """What `room` leaves a message beside the system prompt `persona`."""
    return room - message_tokens({"content": persona}) - _FRAMING


def refusal(persona: str, question: str, room: int) -> Refusal | None:
    """Why `question` can't be sent even alone beside the persona, or None if it can.

    A text too long to fit is refused by its length alone, without reading all of it:
    a chat frame can be megabytes, and this runs on the event loop.
    """
    space = message_room(persona, room)
    if space < MIN_MESSAGE_TOKENS:
        return "context_too_small"
    if fewest_tokens(len(question)) > space or estimate(question) > space:
        return "message_too_long"
    return None


class Fitted(NamedTuple):
    messages: list[dict[str, Any]]
    blocks: int  # how many of the blocks, in order, the system message holds


def turn_messages(
    persona: str, blocks: list[str], recent: list[dict[str, Any]], room: int
) -> Fitted:
    """The system message, then the newest of `recent` that fit, opening on a question.

    `recent` ends with the turn's question, which `refusal` has let through. `blocks`
    are what she recalled for it, most important first; each goes in only if it fits
    beside the question, and none after one that doesn't.
    """
    *history, question = recent
    space = room - message_tokens(question)
    system = persona
    kept = 0
    for block in blocks:
        joined = f"{system}\n\n{block}" if block else system
        if message_tokens({"content": joined}) > space:
            break
        system, kept = joined, kept + 1
    space -= message_tokens({"content": system})
    earlier: list[dict[str, Any]] = []
    for message in reversed(history):
        cost = message_tokens(message)
        if cost > space:
            break
        space -= cost
        earlier.append(message)
    earlier.reverse()
    # A cut by count or by size can open on a reply whose question it cut off, and a
    # failed turn leaves a question with no reply.
    start = next((i for i, m in enumerate(earlier) if m["role"] == "user"), len(earlier))
    return Fitted([{"role": "system", "content": system}, *earlier[start:], question], kept)


class TurnWindow:
    """One turn's prompt, kept inside the window through its tool rounds.

    Holds the list `stream_reply` sends and appends to: the system message, the
    conversation, the question, then each round's call and results.
    """

    def __init__(self, messages: list[dict[str, Any]], room: int, tools: int = 0) -> None:
        self._messages = messages
        self._room = room
        self._tools = tools  # what the tool list takes, for the server's count
        self._question = messages[-1]
        self._costs: dict[int, tuple[dict[str, Any], int]] = {}

    def result_budget(self, calls: int) -> int:
        """What each of the round's `calls` results may take: the room left once the
        older conversation has made way, shared out."""
        kept = [self._messages[0], *self._messages[self._asked() :]]
        free = self._room - sum(map(self._tokens, kept)) - calls * _FRAMING
        return max(0, free // max(calls, 1))

    def make_room(self) -> bool:
        """Drop the oldest of the conversation until the prompt fits, and a reply left
        opening it. If the turn's own rounds still don't fit, shorten them: the long
        arguments of its calls, then the results of all but the last round. False when
        even that leaves the prompt over: then no more rounds with tools."""
        while self._total() > self._room and self._asked() > 1:
            self._messages.pop(1)
        while self._asked() > 1 and self._messages[1]["role"] != "user":
            self._messages.pop(1)
        if self._total() > self._room:
            self._shorten_rounds()
        return self._total() <= self._room

    def observe(self, counted: int, *, with_tools: bool = True) -> None:
        """The server's count of what it was sent, against the estimate: an estimate
        below it is recorded, so a text or a model the rules don't describe shows."""
        estimated = self._total() + (self._tools if with_tools else 0)
        if counted > estimated:
            logger.warning(
                "chat window estimated %d tokens; the model counted %d", estimated, counted
            )
            current_blackbox().record(
                "warn", "chat.window_under", estimated=estimated, counted=counted
            )

    def _shorten_rounds(self) -> None:
        start = self._asked() + 1
        calls = [
            i for i in range(start, len(self._messages)) if self._messages[i].get("tool_calls")
        ]
        for index in range(start, len(self._messages)):
            message = self._messages[index]
            if message.get("tool_calls"):
                shortened = [_short_call(call) for call in message["tool_calls"]]
                self._messages[index] = {**message, "tool_calls": shortened}
            elif message.get("role") == "tool" and calls and index < calls[-1]:
                self._messages[index] = {**message, "content": _EARLIER_RESULT}

    def _total(self) -> int:
        return sum(map(self._tokens, self._messages))

    def _tokens(self, message: dict[str, Any]) -> int:
        # Each round asks for every message's cost again: counted once a turn.
        cached = self._costs.get(id(message))
        if cached is None or cached[0] is not message:
            cached = (message, message_tokens(message))
            self._costs[id(message)] = cached
        return cached[1]

    def _asked(self) -> int:
        # By identity: an earlier message can be word for word the same question.
        return next(i for i, m in enumerate(self._messages) if m is self._question)


def message_tokens(message: dict[str, Any]) -> int:
    """What a message takes of the window, the template's own tokens with it."""
    total = _FRAMING + estimate(str(message.get("content") or ""))
    if message.get("tool_calls"):
        total += estimate(json.dumps(message["tool_calls"]))
    return total


def _short_call(call: dict[str, Any]) -> dict[str, Any]:
    """`call` with each long text argument written as its length: it has run, and its
    result says what it did."""
    function = call.get("function")
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(arguments, str):  # some models send them as JSON text
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return call
    if not isinstance(function, dict) or not isinstance(arguments, dict):
        return call
    short = {
        key: f"[{len(value):,} characters]"
        if isinstance(value, str) and len(value) > _LONG_ARGUMENT
        else value
        for key, value in arguments.items()
    }
    return {**call, "function": {**function, "arguments": short}}
