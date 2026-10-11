"""Streaming LLM chunk type surfaced by the Ollama adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """What one generation cost, from Ollama's counters on its final line.

    The prompt count is what sizes the context window (`LlmSettings.num_ctx`).
    """

    prompt_tokens: int
    prompt_eval_ms: float
    eval_tokens: int
    eval_ms: float


@dataclass(frozen=True, slots=True)
class LlmChunk:
    """One streamed step from the LLM: a content delta and/or tool calls.

    ``done`` marks the terminal chunk. ``tool_calls`` carry the accumulated
    OpenAI-style function calls once the model has finished emitting them. The
    terminal chunk also says why the generation ended (``done_reason``: "stop", or
    "length" when it reached ``num_predict``) and, when the server reports it, what
    it cost.
    """

    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    done: bool = False
    done_reason: str | None = None
    usage: LlmUsage | None = None
