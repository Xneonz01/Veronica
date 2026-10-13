"""Streaming Ollama adapter: content streaming, tool-call accumulation, errors."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaModelNoToolsError,
    OllamaUnreachableError,
)
from app.adapters.llm.tokens import estimate
from app.core.config import get_settings
from app.core.config_llm import MIN_PROMPT_TOKENS, UNLIMITED_REPLY_TOKENS, LlmSettings
from app.services.chat.window import MIN_MESSAGE_TOKENS, message_tokens
from eval import window


def _ndjson(objs: list[dict]) -> bytes:
    return b"".join((json.dumps(obj) + "\n").encode() for obj in objs)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> OllamaLLMClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OllamaLLMClient(LlmSettings(), client=http)


async def test_streams_content_and_accumulates_tool_calls() -> None:
    stream = [
        {"message": {"content": "I'll"}, "done": False},
        {"message": {"content": " check"}, "done": False},
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"location": "Paris"}}}
                ],
            },
            "done": False,
        },
        {"message": {"content": ""}, "done": True},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["think"] is False  # qwen3 reasoning disabled for latency
        assert body["keep_alive"]  # model pinned warm
        assert body["tools"]  # tools forwarded to Ollama
        return httpx.Response(200, content=_ndjson(stream))

    llm = _client(handler)
    chunks = [
        c
        async for c in llm.stream_chat(
            [{"role": "user", "content": "weather?"}],
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )
    ]

    assert [c.content for c in chunks if c.content] == ["I'll", " check"]
    done = [c for c in chunks if c.done]
    assert len(done) == 1
    assert done[0].tool_calls
    assert done[0].tool_calls[0]["function"]["name"] == "get_weather"


async def test_generate_yields_only_content() -> None:
    stream = [
        {"message": {"content": "Hello"}, "done": False},
        {"message": {"content": " world"}, "done": True},
    ]
    llm = _client(lambda _: httpx.Response(200, content=_ndjson(stream)))
    out = [token async for token in llm.generate([{"role": "user", "content": "hi"}])]
    assert "".join(out) == "Hello world"


async def test_unreachable_raises_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    llm = _client(handler)
    with pytest.raises(OllamaUnreachableError):
        _ = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]


async def test_a_model_ollama_does_not_have_is_its_own_error() -> None:
    """Bug 30: it read as a failed turn, which asking again can never mend (mutation:
    let the 404 raise as an HTTP error). Ollama 0.33.3's answer, as measured."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'qwen3.5:9b' not found"})

    llm = _client(handler)
    with pytest.raises(OllamaModelMissingError) as missing:
        _ = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]
    assert missing.value.model == LlmSettings().model
    assert "ollama pull" in str(missing.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="404 page not found"),  # a wrong base_url: Ollama's router
        httpx.Response(404, json={"detail": "Not Found"}),  # something else on the port
        httpx.Response(404, json=["model not found"]),
        httpx.Response(404, json={"error": "route disabled by the proxy"}),
    ],
)
async def test_a_404_that_isnt_ollamas_own_is_not_a_missing_model(
    response: httpx.Response,
) -> None:
    """Pulling a model would never mend a wrong address (mutation: read any 404 as it)."""
    llm = _client(lambda _request: response)
    with pytest.raises(httpx.HTTPStatusError):
        _ = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]


async def test_graceful_early_break_closes_cleanly() -> None:
    stream = [{"message": {"content": str(i)}, "done": False} for i in range(10)]
    llm = _client(lambda _: httpx.Response(200, content=_ndjson(stream)))

    got: list[str | None] = []
    async for chunk in llm.stream_chat([{"role": "user", "content": "count"}]):
        got.append(chunk.content)
        if len(got) == 2:
            break  # barge-in: stop consuming mid-stream
    assert got == ["0", "1"]


async def _free(llm: OllamaLLMClient) -> bool:
    try:
        await asyncio.wait_for(llm.activity.quiet(0), 0.05)
    except TimeoutError:
        return False
    return True


async def test_the_model_is_busy_while_a_reply_streams_and_free_once_it_ends() -> None:
    """What the learning worker waits on (`core/model_activity.py`)."""
    stream = [{"message": {"content": str(i)}, "done": i == 2} for i in range(3)]
    llm = _client(lambda _: httpx.Response(200, content=_ndjson(stream)))
    async for _chunk in llm.stream_chat([{"role": "user", "content": "count"}]):
        assert not await _free(llm)
    assert await _free(llm)

    # A consumer that stops early frees it when the stream is closed.
    partial = llm.stream_chat([{"role": "user", "content": "count"}])
    await anext(partial)
    assert not await _free(llm)
    await partial.aclose()
    assert await _free(llm)


async def test_a_background_request_does_not_count_as_the_model_in_use() -> None:
    """Learning's own requests: counted, they would hold learning itself up, and a reply
    starting during one couldn't be told apart from it (mutation: count them)."""
    stream = [{"message": {"content": str(i)}, "done": i == 2} for i in range(3)]
    llm = _client(lambda _: httpx.Response(200, content=_ndjson(stream)))
    chunks = []
    async for chunk in llm.background().stream_chat([{"role": "user", "content": "count"}]):
        assert await _free(llm)
        chunks.append(chunk.content)
    assert chunks == ["0", "1", "2", None]  # None: the closing chunk


async def test_a_request_that_fails_leaves_the_model_free() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    llm = _client(refuse)
    with pytest.raises(OllamaUnreachableError):
        _ = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]
    assert await _free(llm)


async def test_the_context_window_and_reply_limit_are_sent() -> None:
    # Left to Ollama's default of 4,096, an oversized prompt loses its front, the
    # persona and tool rules, without any error.
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content)["options"])
        return httpx.Response(200, content=_ndjson([{"message": {"content": ""}, "done": True}]))

    llm = OllamaLLMClient(
        LlmSettings(num_ctx=8192, num_predict=2048),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
    )
    _ = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]
    assert seen["num_ctx"] == 8192 and seen["num_predict"] == 2048


async def test_no_reply_limit_is_sent_when_it_is_zero() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content)["options"])
        return httpx.Response(200, content=_ndjson([{"message": {"content": ""}, "done": True}]))

    llm = OllamaLLMClient(
        LlmSettings(num_predict=0),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
    )
    _ = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]
    assert "num_predict" not in seen


async def test_the_final_chunk_says_why_it_stopped_and_what_it_cost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    final = {
        "message": {"content": ""},
        "done": True,
        "done_reason": "length",
        "prompt_eval_count": 3033,
        "prompt_eval_duration": 18_400_000,
        "eval_count": 2048,
        "eval_duration": 900_000_000,
    }
    llm = _client(lambda _request: httpx.Response(200, content=_ndjson([final])))
    chunks = [c async for c in llm.stream_chat([{"role": "user", "content": "go on"}])]
    done = chunks[-1]
    assert done.done_reason == "length"
    assert done.usage is not None and done.usage.prompt_tokens == 3033
    assert done.usage.prompt_eval_ms == pytest.approx(18.4)
    assert "cut short" in caplog.text  # a cut-off reply is said, not passed off as whole


async def test_usage_is_unknown_rather_than_zero_when_the_server_omits_it() -> None:
    llm = _client(
        lambda _request: httpx.Response(200, content=_ndjson([{"message": {}, "done": True}]))
    )
    chunks = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]
    assert chunks[-1].usage is None and chunks[-1].done_reason is None


async def _use(llm: OllamaLLMClient) -> None:
    async for _ in llm.stream_chat([{"role": "user", "content": "hi"}]):
        pass


async def test_the_model_is_released_at_quit_and_a_dead_server_does_not_hold_it_up() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(200, content=_ndjson([{"message": {}, "done": True}]))
        assert request.url.path == "/api/generate"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    llm = _client(handler)
    await _use(llm)
    assert await llm.release_model() is True
    assert sent == [{"model": LlmSettings().model, "keep_alive": 0}]

    def down(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(200, content=_ndjson([{"message": {}, "done": True}]))
        raise httpx.ConnectError("refused")

    dead = _client(down)
    await _use(dead)
    assert await dead.release_model() is False


async def test_a_model_it_never_used_is_left_alone() -> None:
    # Ollama is shared. Every test that started and stopped the app unloaded the user's
    # model, and quitting unloaded one another program had loaded.
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={})

    assert await _client(handler).release_model() is False
    assert requests == []


#: Characters per token on Veronica's largest prompts, measured with
#: `scripts/prompt_probe.py` (2026-09-24, Ollama 0.33.3): qwen3.5:9b 3.88 on both lanes
#: (chat 5,059 tokens, call 3,011), qwen3:8b 4.11 and 4.29; again on 2026-09-25,
#: qwen3.5:9b 3.92 in chat, 3.95 on a call and 4.19 for learning; and on 2026-09-27,
#: with learning's largest prompt a later part's (the turns before it framed), 3.94,
#: 3.95 and 4.20; the call's at 0.34.13, its style naming the actions (bug 50), 3.96;
#: and on 2026-10-13 with web search on, its rules and tools (0.35.0), 3.96, 3.98, 4.20.
#: Set just under the lowest reading, so the estimate never comes out below the
#: server's own count.
CHARS_PER_TOKEN = 3.85

#: What each lane's largest prompt measured on the server, for the tripwire below.
MEASURED_TOKENS = {"chat": 5691, "call": 3524, "learning": 5272}


def test_the_largest_prompt_fits_the_window_with_the_longest_reply_to_spare() -> None:
    # A window smaller than the prompt isn't an error: the persona quietly stops being
    # sent, and the only symptom is a model that got worse at choosing tools. The chat
    # window cuts the conversation by its own estimate, which runs over; by the
    # server's count the largest prompt fits beside the longest reply.
    for lane, prompt in window.largest(get_settings()).items():
        tokens = round(window.characters(prompt) / CHARS_PER_TOKEN)
        assert tokens <= MEASURED_TOKENS[lane] * 1.15, (
            f"the largest {lane} prompt is now about {tokens} tokens against the "
            f"{MEASURED_TOKENS[lane]} measured: re-measure with scripts.prompt_probe and "
            "re-size LlmSettings.num_ctx"
        )
        assert tokens + LlmSettings().reply_tokens <= LlmSettings().num_ctx, (lane, tokens)


def test_the_largest_prompt_is_the_one_with_web_search_on() -> None:
    """Its rules and its tools make each lane's prompt its largest: measured without
    them, the window's floor left no room for a message once it was on (mutations: drop
    the web's tools; drop its rules)."""
    prompts = window.largest(get_settings())
    offered = {
        lane: {tool["function"]["name"] for tool in prompts[lane][1]} for lane in ("chat", "call")
    }

    assert {"web_search", "web_read"} <= offered["chat"] and "web_search" in offered["call"]
    for lane in ("chat", "call"):
        assert "You can search the web" in prompts[lane][0][0]["content"]


def test_the_smallest_window_allowed_holds_her_longest_persona_and_a_message() -> None:
    """What `LlmSettings` refuses below: the longest persona, every tool, memory and
    documents at their budgets and a message still fit it, by the chat window's own
    count (mutation: lower it)."""
    messages, tools = window.largest(get_settings())["chat"]
    fixed = message_tokens(messages[0]) + estimate(json.dumps(tools))

    assert fixed + message_tokens({"content": ""}) + MIN_MESSAGE_TOKENS <= MIN_PROMPT_TOKENS


def test_a_window_too_small_for_her_prompt_is_refused_at_start() -> None:
    """With it every message would be sent back as too long (mutation: accept any)."""
    with pytest.raises(ValidationError, match="num_ctx"):
        LlmSettings(num_ctx=4096)
    with pytest.raises(ValidationError, match="beside the longest reply"):
        LlmSettings(num_ctx=7000, num_predict=2048)
    assert LlmSettings(num_ctx=7000, num_predict=1024).reply_tokens == 1024
    assert LlmSettings(num_predict=0).reply_tokens == UNLIMITED_REPLY_TOKENS


async def test_a_request_can_ask_for_its_own_temperature() -> None:
    """Learning writes a record, not a reply (bug 48); the settings' is sent otherwise
    (mutation: always the settings')."""
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["options"]["temperature"])
        return httpx.Response(200, content=_ndjson([{"message": {"content": ""}, "done": True}]))

    llm = OllamaLLMClient(
        LlmSettings(temperature=0.7),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test"),
    )
    _ = [c async for c in llm.stream_chat([{"role": "user", "content": "hi"}])]
    _ = [
        c
        async for c in llm.background().stream_chat(
            [{"role": "user", "content": "hi"}], temperature=0.2
        )
    ]
    assert seen == [0.7, 0.2]


async def test_a_model_that_cant_use_tools_is_its_own_error() -> None:
    """Bug 45: every turn failed as a passing hiccup (mutation: let the 400 raise as an
    HTTP error). Ollama 0.33.3's answer to a model without tools, as measured."""

    def handler(_request: httpx.Request) -> httpx.Response:
        error = "registry.ollama.ai/library/smollm:135m does not support tools"
        return httpx.Response(400, json={"error": error})

    with pytest.raises(OllamaModelNoToolsError) as refused:
        _ = [c async for c in _client(handler).stream_chat([{"role": "user", "content": "hi"}])]
    assert refused.value.model == LlmSettings().model


async def test_a_model_ollama_couldnt_run_is_its_own_error() -> None:
    """Bug 45: a call's warm-up failed as "check that the backend is running" (mutation:
    let the 500 raise as an HTTP error). Ollama 0.33.3's answer, as measured."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"error": "llama-server process has terminated: exit status 1"}
        )

    with pytest.raises(OllamaModelFailedError) as failed:
        _ = [c async for c in _client(handler).stream_chat([{"role": "user", "content": "hi"}])]
    assert failed.value.model == LlmSettings().model
    assert "llama-server process has terminated" in str(failed.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "invalid options"}),  # not the model's doing
        httpx.Response(502, text="Bad Gateway"),  # a proxy's page, not Ollama's
        httpx.Response(500, json={"detail": "Internal Server Error"}),  # someone else's
    ],
)
async def test_other_failures_stay_plain_failures(response: httpx.Response) -> None:
    """Only Ollama's own words name a cause (mutations: any 400 as no tools; any 5xx as a
    model that couldn't run)."""
    with pytest.raises(httpx.HTTPStatusError):
        _ = [
            c
            async for c in _client(lambda _request: response).stream_chat(
                [{"role": "user", "content": "hi"}]
            )
        ]
