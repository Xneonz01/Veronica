"""One model turn through Veronica's own Ollama adapter, timed.

The adapter is the product's, so a turn carries the same request body, `think`
setting and tool format a real one does: a result is about the model, not about a
harness that talks to it differently.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from app.adapters.llm.ollama_client import OllamaLLMClient


@dataclass(slots=True)
class Turn:
    text: str
    calls: list[dict[str, Any]]
    # To the first word or tool call: what someone waiting for an answer feels.
    first_ms: float | None
    total_ms: float

    @property
    def names(self) -> list[str]:
        return [call_name(call) for call in self.calls]


def call_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    return str(function.get("name", "")) if isinstance(function, dict) else ""


def call_arguments(call: dict[str, Any]) -> dict[str, Any]:
    """The call's arguments as a mapping; a model may send them as a JSON string."""
    function = call.get("function")
    raw = function.get("arguments", {}) if isinstance(function, dict) else {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


async def run_turn(
    llm: OllamaLLMClient,
    messages: list[dict[str, Any]],
    *,
    model: str,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> Turn:
    started = time.perf_counter()
    first: float | None = None
    parts: list[str] = []
    calls: list[dict[str, Any]] = []
    async for chunk in llm.stream_chat(messages, tools=tools, model=model, temperature=temperature):
        if first is None and (chunk.content or chunk.tool_calls):
            first = (time.perf_counter() - started) * 1000.0
        if chunk.content:
            parts.append(chunk.content)
        if chunk.tool_calls:
            calls.extend(chunk.tool_calls)
    return Turn("".join(parts), calls, first, (time.perf_counter() - started) * 1000.0)
