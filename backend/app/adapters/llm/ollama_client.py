"""Streaming Ollama LLM adapter using the native /api/chat endpoint.

We use /api/chat (not the OpenAI-compatible /v1/chat/completions): the /v1 path
silently drops tool_calls when streaming. Responses are newline-delimited JSON;
each line carries an incremental message.content and, when the model decides to
call a tool, a message.tool_calls array that we accumulate and surface on the
terminal chunk.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Any

import httpx

from app.adapters.llm.ollama_errors import (
    OllamaStreamError,
    OllamaUnreachableError,
    raise_refusal,
)
from app.adapters.llm.types import LlmChunk, LlmUsage
from app.core.config_llm import LlmSettings
from app.core.model_activity import ModelActivity

logger = logging.getLogger(__name__)


def _usage(done: dict[str, Any]) -> LlmUsage | None:
    """Ollama's counters from the final line, durations in milliseconds.

    They arrive in nanoseconds, and are missing from a server that doesn't report
    them. None then, never zeros: zeros would say the prompt cost nothing.
    """
    prompt_tokens = done.get("prompt_eval_count")
    eval_tokens = done.get("eval_count")
    if prompt_tokens is None or eval_tokens is None:
        return None
    return LlmUsage(
        prompt_tokens=int(prompt_tokens),
        prompt_eval_ms=float(done.get("prompt_eval_duration", 0)) / 1e6,
        eval_tokens=int(eval_tokens),
        eval_ms=float(done.get("eval_duration", 0)) / 1e6,
    )


class OllamaLLMClient:
    """Streams chat completions (with tool-calling) from a local Ollama server."""

    def __init__(self, settings: LlmSettings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        # Separate connect and read timeouts instead of one flat budget:
        #   * connect is short: Ollama is local, so it is either up or not, and a quick
        #     failure lets the UI say so promptly.
        #   * read covers a cold load of the model before the first byte (typically the
        #     first message of a session), then bounds the gap between chunks.
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(
                settings.request_timeout_s,
                connect=settings.connect_timeout_s,
                read=settings.load_timeout_s,
            ),
        )
        # The models this client has asked for: the only ones `release_model` frees.
        self._used: set[str] = set()
        # Held while a request someone waits on streams: learning waits for a quiet
        # model, and gives way when one starts (`chat/learning_worker.py`).
        self.activity = ModelActivity()

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        keep_alive: str | None = None,
        counted: bool = True,
        temperature: float | None = None,
    ) -> AsyncIterator[LlmChunk]:
        """Yield content deltas, then a terminal chunk carrying any tool calls.

        `counted` marks the model in use while it streams (`activity`); only work
        nobody waits on passes False, through `background`. `temperature` replaces
        the settings' for this request: learning writes a record, not a reply.
        """
        body: dict[str, Any] = {
            "model": model or self._settings.model,
            "messages": messages,
            "stream": True,
            # think=False disables qwen3's default reasoning trace, which adds seconds of
            # latency. keep_alive pins the model in VRAM so later turns stay warm.
            "think": self._settings.think,
            "keep_alive": keep_alive or self._settings.keep_alive,
            # The context window is sent, not left to Ollama's default (4,096 here),
            # which doesn't refuse an oversized prompt: it drops the front of it, the
            # persona and the tool rules. `LlmSettings.num_ctx` says how it was sized.
            "options": {
                "temperature": self._settings.temperature if temperature is None else temperature,
                "num_ctx": self._settings.num_ctx,
            },
        }
        if self._settings.num_predict > 0:
            # The runaway guard (`LlmSettings.num_predict`); 0 sends no limit at all.
            body["options"]["num_predict"] = self._settings.num_predict
        if tools:
            body["tools"] = tools
        self._used.add(body["model"])

        tool_calls: list[dict[str, Any]] = []
        try:
            with self.activity.busy() if counted else nullcontext():
                async with self._client.stream("POST", "/api/chat", json=body) as response:
                    if response.is_error:
                        # A refusal asking again can't mend has its own error (bug 45).
                        await raise_refusal(response, body["model"])
                        response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        # Ollama reports a mid-generation failure (model evicted, OOM) as a
                        # 200 with a bare {"error": ...} line, so raise_for_status never sees
                        # it. Without this check the stream just ends without `done` and a
                        # truncated reply is treated as complete.
                        if "error" in data:
                            raise OllamaStreamError(str(data["error"]))
                        message = data.get("message") or {}
                        if message.get("tool_calls"):
                            tool_calls.extend(message["tool_calls"])
                        content = message.get("content") or ""
                        if content:
                            yield LlmChunk(content=content)
                        if data.get("done"):
                            reason = data.get("done_reason")
                            if reason == "length":
                                # A reply cut off at the limit is cut mid-sentence; said in
                                # the log, since everything above would take it as finished.
                                logger.warning(
                                    "generation stopped at num_predict=%d: the reply is cut short",
                                    self._settings.num_predict,
                                )
                            yield LlmChunk(
                                done=True,
                                tool_calls=tool_calls or None,
                                done_reason=str(reason) if reason is not None else None,
                                usage=_usage(data),
                            )
                            return
                    raise OllamaStreamError("the model stream ended without completing")
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OllamaUnreachableError(
                f"Ollama not reachable at {self._settings.base_url} — is `ollama serve` running?"
            ) from exc
        except httpx.ReadTimeout as exc:
            # Reachable but silent for the whole window: almost always a model still
            # loading. The user-facing outcome is the same as unreachable, but the
            # message says which happened so the log is useful.
            raise OllamaUnreachableError(
                f"Ollama accepted the request but sent nothing for "
                f"{self._settings.load_timeout_s:.0f}s (model still loading?)"
            ) from exc

    def background(self) -> BackgroundLlm:
        """This client for work nobody waits on: learning. Its requests don't mark
        the model in use, so they don't hold learning itself up, and a reply or a call
        that starts during one can be told apart from it."""
        return BackgroundLlm(self)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """LLMProvider contract: yield assistant content tokens as they stream."""
        async for chunk in self.stream_chat(messages, tools):
            if chunk.content:
                yield chunk.content

    async def release_model(self, model: str | None = None) -> bool:
        """Ask Ollama to free the model's VRAM now. Best-effort, and quick.

        Needed at quit because `keep_alive` does its job: without this, closing
        Veronica leaves the model resident for half an hour in a process she doesn't
        own. `stream_chat` can't do it, since its `keep_alive or ...` would turn a 0
        back into the default. A busy or absent Ollama must not hold up exit.

        Only a model this client has used. Ollama is shared: another program may have
        the same model loaded, and every test that started the app and stopped it
        again unloaded the model from the user's own Ollama.
        """
        target = model or self._settings.model
        if target not in self._used:
            return False
        try:
            response = await self._client.post(
                "/api/generate",
                json={"model": target, "keep_alive": 0},
                timeout=httpx.Timeout(5.0, connect=2.0),
            )
        except Exception as exc:  # Ollama already gone: its VRAM went with it
            logger.debug("release of %s skipped: %r", target, exc)
            return False
        logger.info("released %s from Ollama (status %s)", target, response.status_code)
        return response.status_code < 500

    async def aclose(self) -> None:
        """Hard cancel / shutdown: close the underlying HTTP client."""
        await self._client.aclose()


class BackgroundLlm:
    """`OllamaLLMClient.background`: the same streams, not counted as use."""

    def __init__(self, client: OllamaLLMClient) -> None:
        self._client = client

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        keep_alive: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LlmChunk]:
        return self._client.stream_chat(
            messages,
            tools,
            model=model,
            keep_alive=keep_alive,
            counted=False,
            temperature=temperature,
        )
