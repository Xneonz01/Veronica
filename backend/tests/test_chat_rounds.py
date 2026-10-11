"""A turn's own tool rounds against the window (`chat/window.py` `TurnWindow`): when the
older conversation has made way and the prompt is still over, the rounds are
shortened (a call's long text, then the results before the last round); when even that
doesn't fit, she answers without tools. And a count from the server above the estimate
is recorded."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.types import LlmChunk, LlmUsage
from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityResult
from app.services.chat import window
from app.services.chat.replies import stream_reply
from app.services.chat.window import TurnWindow, message_tokens
from app.services.tools import ToolBroker
from app.services.tools.render import _LEFT_OUT, render_result
from tests.fakes import build_fake_kernel_runtime, build_fake_memory_runtime

PERSONA = "Persona."
NOTE = "word " * 1900


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": arguments}}


def _cost(messages: list[dict[str, Any]]) -> int:
    return sum(map(message_tokens, messages))


def test_a_call_that_carried_long_text_is_shortened_to_fit() -> None:
    """Saving a pasted note repeats it whole in the call: with nothing older to drop,
    the next round was over (mutation: no shortening of the turn's own rounds)."""
    system = {"role": "system", "content": PERSONA}
    question = {"role": "user", "content": f"Save this as notes.md: {NOTE}"}
    messages = [system, question]
    room = _cost(messages) + 300
    turn = TurnWindow(messages, room)
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_call("files_write", path="notes.md", content=NOTE)],
        }
    )
    messages.append({"role": "tool", "content": "Wrote notes.md (9,500 chars)."})

    assert turn.make_room() is True

    shortened = messages[2]["tool_calls"][0]["function"]["arguments"]
    assert shortened == {"path": "notes.md", "content": "[9,500 characters]"}
    assert _cost(messages) <= room


def test_results_before_the_last_round_give_way_first() -> None:
    """(mutation: leave the earlier results; drop the last round's too)"""
    system = {"role": "system", "content": PERSONA}
    question = {"role": "user", "content": "Compare the two files."}
    first = {"role": "tool", "content": "text " * 800}
    last = {"role": "tool", "content": "text " * 800}
    messages = [system, question]
    room = _cost(messages) + 1000
    turn = TurnWindow(messages, room)
    messages += [
        {"role": "assistant", "content": "", "tool_calls": [_call("files_read", path="a.md")]},
        first,
        {"role": "assistant", "content": "", "tool_calls": [_call("files_read", path="b.md")]},
        last,
    ]

    assert turn.make_room() is True

    assert "left out" in messages[3]["content"] and messages[5] is last


class _Recording:
    """A model that answers as scripted and keeps whether each request offered tools."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = script
        self.offered: list[bool] = []

    async def stream_chat(
        self, messages: list[dict[str, Any]], tools: Any = None, *, model: Any = None
    ) -> AsyncIterator[LlmChunk]:
        self.offered.append(bool(tools))
        step = self.script.pop(0)
        for token in step.get("tokens", []):
            yield LlmChunk(content=token)
        yield LlmChunk(done=True, tool_calls=step.get("tool_calls"), usage=step.get("usage"))


async def test_with_no_room_for_another_round_she_answers_without_tools(tmp_path: Path) -> None:
    """A question that fills the window leaves no room for a round: the next request
    goes without the tool list, and she answers from what she has (mutation: keep
    offering tools)."""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    kernel = await build_fake_kernel_runtime(memory, InProcessEventBus())
    broker = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": NOTE}]
    llm = _Recording(
        [{"tool_calls": [_call("memory_recall", query="notes")]}, {"tokens": ["From what I have."]}]
    )

    async def ignore(*_args: Any) -> None:
        return None

    reply = await stream_reply(
        llm,  # type: ignore[arg-type]
        broker,
        messages,
        model="m",
        send=ignore,
        on_tool=ignore,
        window=TurnWindow(messages, _cost(messages)),
    )

    assert llm.offered == [True, False] and reply == "From what I have."
    await memory.store.close()


def test_a_count_above_the_estimate_is_recorded(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A text or a model the rules don't describe shows, as numbers (mutation: never
    compare)."""
    events: list[tuple[str, dict[str, object]]] = []

    class _Box:
        def record(self, _level: str, event: str, **fields: object) -> None:
            events.append((event, fields))

    monkeypatch.setattr(window, "current_blackbox", lambda: _Box())
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": "hi"}]
    turn = TurnWindow(messages, 1000, tools=50)
    estimated = _cost(messages) + 50

    with caplog.at_level(logging.WARNING):
        turn.observe(estimated)  # at the estimate: nothing to say
        turn.observe(estimated + 1)

    assert events == [("chat.window_under", {"estimated": estimated, "counted": estimated + 1})]
    assert len(caplog.records) == 1


def test_a_budget_too_small_for_the_fence_gets_the_note_alone() -> None:
    """The fence alone is about 90 tokens: a result given less used to take that anyway
    (mutation: fence the note whatever the budget)."""
    result = CapabilityResult(ok=True, data={"content": "text " * 100})

    assert render_result("files.read", result, budget=20) == _LEFT_OUT


class _NoTools:
    def schemas(self) -> list[dict[str, Any]]:
        return []


async def test_usage_from_the_server_reaches_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """(mutation: never pass the server's count on)"""
    seen: list[int] = []
    monkeypatch.setattr(TurnWindow, "observe", lambda _self, counted, **_: seen.append(counted))
    usage = LlmUsage(prompt_tokens=123, prompt_eval_ms=1.0, eval_tokens=1, eval_ms=1.0)
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": "hi"}]

    async def ignore(*_args: Any) -> None:
        return None

    await stream_reply(
        _Recording([{"tokens": ["Hi."], "usage": usage}]),  # type: ignore[arg-type]
        _NoTools(),  # type: ignore[arg-type]
        messages,
        model="m",
        send=ignore,
        on_tool=ignore,
        window=TurnWindow(messages, 1000),
    )

    assert seen == [123]


async def test_what_the_last_round_fetched_is_put_into_words(tmp_path: Path) -> None:
    """Bug 25: still asking for tools at the last round, with a sentence each time, the
    model's last sentence was the reply and what that round fetched went unsaid
    (mutation: return the last round's sentence)."""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    kernel = await build_fake_kernel_runtime(memory, InProcessEventBus())
    broker = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": "Notes?"}]
    llm = _Recording(
        [
            {"tokens": ["Let me look."], "tool_calls": [_call("memory_recall", query="notes")]},
            {"tokens": ["And again."], "tool_calls": [_call("memory_recall", query="lists")]},
            {"tokens": ["Once more."], "tool_calls": [_call("memory_recall", query="plans")]},
            {"tokens": ["Here is what I found."]},
        ]
    )

    async def ignore(*_args: Any) -> None:
        return None

    reply = await stream_reply(
        llm,  # type: ignore[arg-type]
        broker,
        messages,
        model="m",
        send=ignore,
        on_tool=ignore,
    )

    assert reply == "Here is what I found." and llm.offered == [True, True, True, False]
    await memory.store.close()


async def test_a_silent_last_round_keeps_the_sentence_before_it(tmp_path: Path) -> None:
    """Asked to put the last round into words, a model that wrote nothing left an empty
    answer, stored as one (mutation: the tool-less round's text alone)."""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    kernel = await build_fake_kernel_runtime(memory, InProcessEventBus())
    broker = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": "Notes?"}]
    llm = _Recording(
        [
            {"tokens": ["Let me look."], "tool_calls": [_call("memory_recall", query="notes")]},
            {"tokens": ["And again."], "tool_calls": [_call("memory_recall", query="lists")]},
            {"tokens": ["Once more."], "tool_calls": [_call("memory_recall", query="plans")]},
            {"tokens": []},
        ]
    )

    async def ignore(*_args: Any) -> None:
        return None

    reply = await stream_reply(
        llm,  # type: ignore[arg-type]
        broker,
        messages,
        model="m",
        send=ignore,
        on_tool=ignore,
    )

    assert reply == "Once more." and llm.offered == [True, True, True, False]
    await memory.store.close()
