"""When the search service can't be reached, and when a reply written without tools
comes out as a tool call.

With no search service running, another local model searched three times in each of
9 turns and spent every round on it; asked then to answer without tools, it wrote its
next call out as text, and that was shown as her answer (found in use of 0.35.0).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.types import LlmChunk
from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.services.chat.replies import stream_reply
from app.services.tools import ToolBroker, ToolOutcome, went_down
from app.services.tools.markup import LAST_ROUND, NOT_FINISHED, MarkupGate
from app.services.voice.realtime import workers
from app.services.voice.realtime.session import RealtimeSession
from tests.fakes import (
    FakeDetector,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    ScriptedLLM,
    build_fake_kernel_runtime,
    build_fake_memory_runtime,
    build_fake_runtime,
)

SEARCH = {"function": {"name": "web_search", "arguments": {"query": "weather in springfield"}}}
WRITTEN_CALL = '<|tool_call>call:web_search{query:<|"|>springfield<|"|>}<tool_call|>'


def _down() -> ToolOutcome:
    return ToolOutcome(
        text="I couldn't reach the search service.",
        ok=False,
        code="search_unavailable",
        capability="web.search",
    )


class _SearchBroker:
    """Offers web search, which always finds the service down, and records each list."""

    def __init__(self) -> None:
        self.offered: list[list[str]] = []

    def schemas(self, without: Any = ()) -> list[dict[str, Any]]:
        names = [] if "web.search" in without else ["web_search"]
        self.offered.append(names)
        return [{"type": "function", "function": {"name": name}} for name in names]

    def will_confirm(self, *_: Any) -> bool:
        return False

    async def execute(self, *_: Any, **__: Any) -> ToolOutcome:
        return _down()


class _Model:
    """Answers as scripted: a step is tool calls, or tokens."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = script
        self.asked: list[list[dict[str, Any]]] = []

    async def stream_chat(self, messages: list[dict[str, Any]], *_: Any, **__: Any) -> Any:
        self.asked.append(list(messages))
        step = self.script.pop(0)
        for token in step.get("tokens", []):
            yield LlmChunk(content=token)
        yield LlmChunk(done=True, tool_calls=step.get("tool_calls"))


async def test_the_last_round_is_told_tools_are_over_and_the_turn_never_keeps_it() -> None:
    """Told nothing, another local model wrote a call as text in 4 of 9 such answers
    (mutations: ask the last round without the note; keep the note in the turn)."""
    broker = _SearchBroker()
    broker.execute = lambda *_, **__: _done(ToolOutcome(text="x", ok=True))  # type: ignore[method-assign]
    model = _Model([{"tool_calls": [SEARCH]}] * 3 + [{"tokens": ["From what I have."]}])

    _, _, turn = await _chat_turn(model, broker)

    assert model.asked[-1][-1] == LAST_ROUND
    assert all(LAST_ROUND not in asked for asked in model.asked[:-1])
    assert LAST_ROUND not in turn


async def _chat(model: _Model, broker: Any) -> tuple[str, str]:
    reply, screen, _ = await _chat_turn(model, broker)
    return reply, screen


async def _chat_turn(model: _Model, broker: Any) -> tuple[str, str, list[dict[str, Any]]]:
    screen: list[str] = []

    async def send(event: dict[str, Any]) -> None:
        if event.get("type") == "token":
            screen.append(event["text"])

    async def ignore(*_: Any) -> None:
        return None

    messages = [{"role": "user", "content": "what's the weather in springfield tomorrow"}]
    reply = await stream_reply(
        model,  # type: ignore[arg-type]
        broker,
        messages,
        model="m",
        send=send,
        on_tool=ignore,
    )
    return reply, "".join(screen), messages


async def test_a_search_service_found_down_is_not_offered_again_that_turn() -> None:
    """(mutation: keep offering it)"""
    broker = _SearchBroker()
    model = _Model([{"tool_calls": [SEARCH]}, {"tokens": ["Search isn't working right now."]}])

    reply, _ = await _chat(model, broker)

    assert broker.offered == [["web_search"], []]
    assert reply == "Search isn't working right now."


async def test_an_answer_that_comes_out_as_a_tool_call_is_never_shown() -> None:
    """With its rounds spent, a model wrote its next call as text and it was the answer
    (mutation: pass the last round's text on as it comes)."""
    working = ToolOutcome(text="Nothing found.", ok=True, capability="memory.recall")
    broker = _SearchBroker()
    broker.execute = lambda *_, **__: _done(working)  # type: ignore[method-assign]
    model = _Model([{"tool_calls": [SEARCH]}] * 3 + [{"tokens": ["<|tool", "_call>call:web"]}])

    reply, screen = await _chat(model, broker)

    assert reply == screen == NOT_FINISHED


async def test_a_short_last_answer_still_reaches_the_screen() -> None:
    """Held until the reply ends (mutation: never pass on what is held at the end)."""
    working = ToolOutcome(text="Nothing found.", ok=True, capability="memory.recall")
    broker = _SearchBroker()
    broker.execute = lambda *_, **__: _done(working)  # type: ignore[method-assign]
    model = _Model([{"tool_calls": [SEARCH]}] * 3 + [{"tokens": ["Done."]}])

    reply, screen = await _chat(model, broker)

    assert reply == screen == "Done."


async def _done(outcome: ToolOutcome) -> ToolOutcome:
    return outcome


def test_only_a_service_that_is_down_counts() -> None:
    """(mutation: any failure takes a capability away)"""
    refused = ToolOutcome(text="No.", ok=False, code="confirm_denied", capability="files.write")
    unnamed = ToolOutcome(text="x", ok=False, code="search_unavailable")

    assert went_down([_down(), refused, unnamed]) == {"web.search"}


async def test_the_broker_leaves_out_what_went_down(tmp_path: Path) -> None:
    """(mutation: ignore `without`)"""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    kernel = await build_fake_kernel_runtime(memory, InProcessEventBus())
    broker = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")
    first = kernel.registry.offered(call=False)[0]

    names = [tool["function"]["name"] for tool in broker.schemas(without={first.name})]

    assert first.tool_name not in names and names
    await memory.store.close()


@pytest.mark.parametrize(
    "reply",
    [
        WRITTEN_CALL,
        '<tool_call>{"name": "web_search"}</tool_call>',
        "\n\n<|python_tag|>web_search.call(query='x')",
        '[TOOL_CALLS][{"name": "web_search"}]',
        "<function=web_search>{}</function>",
        "<tool_call>",
    ],
)
def test_a_reply_that_opens_as_a_call_becomes_a_plain_sentence(reply: str) -> None:
    """`<|tool_call>`, Qwen, Llama, Mistral and function tags, a short one too (mutations: miss
    a family; let the held start through)."""
    gate = MarkupGate()
    out = [gate.feed(reply[i : i + 3]) for i in range(0, len(reply), 3)]

    assert "".join(out) + gate.end() == NOT_FINISHED


@pytest.mark.parametrize(
    "reply",
    [
        "Here is what I found about commercial 3D printing risks.",
        "Done.",
        '{"name": "Rowan", "city": "Lisbon"}',
        "  The <tool_call> tag is how Qwen writes a call.",
    ],
)
def test_words_pass_through_whole_and_in_order(reply: str) -> None:
    """An answer can be JSON, or mention a tag (mutations: block any JSON; drop the held
    start)."""
    gate = MarkupGate()
    out = [gate.feed(reply[i : i + 4]) for i in range(0, len(reply), 4)]
    out.append(gate.feed(None))

    assert "".join(out) + gate.end() == reply and not gate.blocked


async def test_a_call_never_reads_a_tool_call_aloud(tmp_path: Path) -> None:
    """On a call the last round is spoken as it streams (mutation: speak it as it comes)."""
    note = tmp_path / "note.txt"
    note.write_text("x", encoding="utf-8")
    read = {"tool_calls": [{"function": {"name": "files_read", "arguments": {"path": str(note)}}}]}
    llm = ScriptedLLM([read, read, {"tokens": [WRITTEN_CALL]}])
    session, memory = await _call_session(tmp_path, llm)

    await workers.respond(session, "keep reading it")

    spoken = _spoken(session)
    assert "tool_call" not in spoken and NOT_FINISHED in spoken
    assert llm.calls[-1][-1] == LAST_ROUND and LAST_ROUND not in llm.calls[0]
    await memory.store.close()


async def test_a_short_last_answer_on_a_call_is_still_said(tmp_path: Path) -> None:
    """And it counts as her first words (mutations: never speak what is held at the end;
    don't mark the time)."""
    note = tmp_path / "note.txt"
    note.write_text("x", encoding="utf-8")
    read = {"tool_calls": [{"function": {"name": "files_read", "arguments": {"path": str(note)}}}]}
    session, memory = await _call_session(
        tmp_path, ScriptedLLM([read, read, {"tokens": ["Okay."]}])
    )

    await workers.respond(session, "keep reading it")

    assert "Okay." in _spoken(session)
    assert [row["ttft_ms"] is not None for row in session._metrics_sink.rows] == [True]  # type: ignore[attr-defined]
    await memory.store.close()


async def test_a_call_stops_searching_a_service_found_down(tmp_path: Path) -> None:
    """(mutation: keep offering it on a call)"""
    broker = _SearchBroker()
    llm = ScriptedLLM([{"tool_calls": [SEARCH]}, {"tokens": ["Search is down."]}])
    session, memory = await _call_session(tmp_path, llm, tools=broker)

    await workers.respond(session, "what's the weather in springfield tomorrow")

    assert broker.offered[:2] == [["web_search"], []]
    await memory.store.close()


async def _call_session(
    tmp_path: Path, llm: ScriptedLLM, *, tools: Any = None
) -> tuple[RealtimeSession, Any]:
    bus = InProcessEventBus()
    memory = await build_fake_memory_runtime(tmp_path / "db", bus)
    kernel = await build_fake_kernel_runtime(memory, bus, file_roots=[str(tmp_path)])
    broker = tools or ToolBroker(
        kernel.kernel, kernel.registry, actor="veronica:voice", voice_only=True
    )
    session = RealtimeSession(
        FakeWS(),  # type: ignore[arg-type]
        build_fake_runtime(stt=FakeSTT([], None), tts=FakeTTS(), llm=llm),  # type: ignore[arg-type]
        bus,
        get_settings(),
        persona=lambda: "You are Veronica.",
        metrics_sink=ListMetricsSink(),
        detector=FakeDetector([]),
        memory=memory,
        tools=broker,
    )
    return session, memory


def _spoken(session: RealtimeSession) -> str:
    """Everything handed to speech this turn (`tests/test_voice_tools.py` reads it so)."""
    chunks: list[bytes] = []
    while not session.audio_out.empty():
        item = session.audio_out.get_nowait()
        if isinstance(item, bytes):
            chunks.append(item)
    return b"".join(chunks).decode("utf-8", errors="replace")


@pytest.mark.parametrize(
    "chunks",
    [
        ["Let me look again. ", "<|tool_call>call:web_search{query:x}<tool_call|>"],
        ["Let me look again. <|to", "ol_call>call:web_search{query:x}"],
        ["Let me look again.\n[TOOL_CALLS]", '[{"name": "web_search"}]'],
        ["Let me look again.", " <|to", "ol_call>call:web_search{query:x}"],
    ],
)
def test_a_call_written_later_in_the_reply_ends_it(chunks: list[str]) -> None:
    """Words, then a call: the words are kept and the call never shown, split across
    chunks too (mutations: look only at the start; keep no tail back)."""
    gate = MarkupGate()

    out = "".join(gate.feed(chunk) for chunk in chunks) + gate.end()

    assert out == f"Let me look again. {NOT_FINISHED}"
