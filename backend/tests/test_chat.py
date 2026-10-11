"""Text-chat path: memory tools (save/recall) + RAG grounding with citations."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.services.chat import lifecycle
from app.services.chat.session import ChatSession
from app.services.memory.models import MemoryCreate, MemoryKind
from tests.fakes import (
    FakeWS,
    ScriptedLLM,
    build_fake_chat_store,
    build_fake_kernel_runtime,
    build_fake_memory_runtime,
    build_fake_persona,
    build_fake_project_store,
)


def _events(ws: FakeWS) -> list[dict]:
    return [json.loads(text) for text in ws.sent_text]


async def _session(ws, mem, llm, kernel, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Build a ChatSession with a fake persona, chat store and project store."""
    return ChatSession(
        ws,
        mem,
        llm,
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(mem.store),
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]


async def test_chat_save_tool_stores_and_streams(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = ScriptedLLM(
        [
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "memory_save",
                            "arguments": {"content": "The user likes tea", "kind": "preference"},
                        }
                    }
                ]
            },
            {"tokens": ["Got it — ", "I'll remember."]},
        ]
    )
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    session = ChatSession(
        ws,
        mem,
        llm,
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(mem.store),
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]
    await session._turn("remember that I like tea")

    assert any("likes tea" in m.content for m in await mem.service.list())  # stored
    events = _events(ws)
    assert any(e["type"] == "tool" and e["name"] == "memory_save" for e in events)
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Got it — I'll remember."
    await mem.store.close()


async def test_chat_recall_tool_returns_memories(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.add(MemoryCreate(kind=MemoryKind.fact, content="Sourdough baking project"))
    llm = ScriptedLLM(
        [
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "memory_recall",
                            "arguments": {"query": "sourdough project"},
                        }
                    }
                ]
            },
            {"tokens": ["You're on the sourdough."]},
        ]
    )
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    session = ChatSession(
        ws,
        mem,
        llm,
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(mem.store),
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]
    await session._turn("what am I working on?")

    recalls = [e for e in _events(ws) if e["type"] == "tool" and e["name"] == "memory_recall"]
    assert recalls and "Sourdough" in recalls[0]["result"]
    await mem.store.close()


async def test_chat_recall_falls_back_to_inventory(tmp_path: Path) -> None:
    # "what do you know about me" gates to nothing semantically; the explicit recall
    # tool must still list stable self-facts deterministically.
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.service.add(MemoryCreate(kind=MemoryKind.preference, content="The user prefers tea"))
    llm = ScriptedLLM(
        [
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "memory_recall",
                            "arguments": {"query": "what do you know about me"},
                        }
                    }
                ]
            },
            {"tokens": ["You prefer tea."]},
        ]
    )
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    session = ChatSession(
        ws,
        mem,
        llm,
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(mem.store),
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]
    await session._turn("what do you know about me?")

    recalls = [e for e in _events(ws) if e["type"] == "tool" and e["name"] == "memory_recall"]
    assert recalls and "prefers tea" in recalls[0]["result"]
    await mem.store.close()


async def test_chat_rag_grounds_and_cites(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await mem.rag.index_text(
        "Sourdough Guide", "Sourdough starter dough. Feed the starter carefully."
    )
    llm = ScriptedLLM([{"tokens": ["Feed the starter [1]."]}])
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    session = ChatSession(
        ws,
        mem,
        llm,
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(mem.store),
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]
    await session._turn("how do I feed the sourdough starter?")

    citations = [e for e in _events(ws) if e["type"] == "citations"]
    assert citations and citations[0]["sources"][0]["title"] == "Sourdough Guide"
    assert "starter" in llm.calls[0][0]["content"].lower()  # the doc reached the model
    await mem.store.close()


async def test_chat_plain_reply_streams_tokens(tmp_path: Path) -> None:
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = ScriptedLLM([{"tokens": ["Hello", " there!"]}])
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    session = ChatSession(
        ws,
        mem,
        llm,
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(mem.store),
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]
    await session._turn("hi")

    events = _events(ws)
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Hello there!"
    assert events[-1] == {"type": "state", "state": "idle"}
    await mem.store.close()


async def test_chat_strips_invented_citation(tmp_path: Path) -> None:
    # The model appends [1] though nothing was cited; the backend removes it so the
    # authoritative reply event (and history) carry clean text.
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = ScriptedLLM([{"tokens": ["You prefer tea ", "[1]."]}])
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    session = ChatSession(
        ws,
        mem,
        llm,
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        await build_fake_chat_store(mem.store),
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]
    await session._turn("what do I like?")

    reply = next(e for e in _events(ws) if e["type"] == "reply")
    assert reply["text"] == "You prefer tea."
    await mem.store.close()


async def test_the_history_window_opens_on_a_question(tmp_path: Path) -> None:
    """A failed turn left a question with no answer, so the last 16 messages began with a
    reply whose question was cut off (mutation: cut by count alone)."""
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = ScriptedLLM([{"tokens": ["Sure."]}])
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    session = await _session(ws, mem, llm, kernel, tmp_path)
    earlier = [{"role": "user", "content": "q0"}, {"role": "user", "content": "q1 (failed)"}]
    for turn in range(2, 10):
        earlier += [
            {"role": "user", "content": f"q{turn}"},
            {"role": "assistant", "content": f"a{turn}"},
        ]
    lifecycle.reset_history(session, earlier)

    await session._turn("next")

    window = llm.calls[0][1:]
    assert window[0] == {"role": "user", "content": "q3"}  # a2 was cut from its question
    assert window[-1] == {"role": "user", "content": "next"}
    await mem.store.close()


def test_the_app_has_words_for_every_way_a_chat_turn_fails() -> None:
    """A kind with no words there reads as "Something went wrong with that answer", with
    Ask again (mutation: a new kind and no words)."""
    chat = Path(__file__).resolve().parents[1] / "app" / "services" / "chat"
    sent = {
        kind
        for source in chat.glob("*.py")
        for kind in re.findall(r'_fail\(\s*"([a-z_]+)"', source.read_text(encoding="utf-8"))
    }
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "src" / "conversation"
    copy = (frontend / "chatProblems.ts").read_text(encoding="utf-8")

    assert "message_too_long" in sent
    for kind in sent:
        assert f"  {kind}: {{" in copy or f"kind === '{kind}'" in copy, kind
