"""A chat turn against the model's window (`chat/session.py` with `chat/window.py`):
what is refused before anything is saved, what is saved before the search, and what
gives way to a long message."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.services.chat import context, window
from tests.test_chat_persistence import _build, _events

ASKED = "What did I plan for Friday?"


async def test_a_stop_while_she_searches_keeps_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved before the search, as it always was: on screen it stays, and so it must
    on reload (mutation: save it after the search)."""
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["unused"]}])
    searching = asyncio.Event()

    async def slow(*_args: Any, **_kwargs: Any) -> context.TurnContext:
        searching.set()
        await asyncio.Event().wait()
        raise AssertionError("never finishes")

    monkeypatch.setattr(context, "assemble", slow)
    await session._dispatch({"action": "message", "text": ASKED})
    await asyncio.wait_for(searching.wait(), 1)
    await session._dispatch({"action": "stop"})

    assert session._conversation_id is not None
    assert [m.content for m in await store.messages(session._conversation_id)] == [ASKED]
    assert session._history == [{"role": "user", "content": ASKED}]
    await mem.store.close()


async def test_a_message_too_long_is_refused_before_the_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Searching for a message that can't be sent is work thrown away (mutation: check
    after the search)."""
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["unused"]}])
    searched: list[str] = []

    async def search(*args: Any, **_kwargs: Any) -> context.TurnContext:
        searched.append(args[2])
        return context.TurnContext("", "", [])

    monkeypatch.setattr(context, "assemble", search)
    await session._turn("word " * 8000)

    assert [e["kind"] for e in _events(ws) if e["type"] == "error"] == ["message_too_long"]
    assert searched == [] and await store.list() == []
    await mem.store.close()


async def test_a_window_too_small_for_any_message_says_so_and_saves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Not "your message is too long": nothing would be short enough, and the log gives
    the numbers to change, the system prompt's among them (mutations: call it a message
    too long; log the window alone)."""
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["unused"]}])
    monkeypatch.setattr(window, "prompt_room", lambda *_args: 100)

    await session._turn("hi")

    assert [e["kind"] for e in _events(ws) if e["type"] == "error"] == ["context_too_small"]
    system = window.message_tokens({"content": session._persona()})
    assert f"num_ctx=8192, reply=2048, room after tools=100, system prompt={system}" in caplog.text
    assert _events(ws)[-1] == {"type": "state", "state": "idle"}
    assert await store.list() == []
    await mem.store.close()


async def test_documents_that_give_way_to_a_long_message_are_not_cited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The message is answered without them, and a marker the model still writes is
    dropped, as for any source it wasn't given (mutation: keep their citations)."""
    ws, mem, _store, session = await _build(tmp_path, [{"tokens": ["Here it is [1]."]}])
    documents = "doc " * 5000
    citation = {"n": 1, "title": "notes.md", "ordinal": 0, "text": "doc"}

    async def assembled(*_args: Any, **_kwargs: Any) -> context.TurnContext:
        return context.TurnContext("She keeps a garden.", documents, [citation])

    monkeypatch.setattr(context, "assemble", assembled)
    await session._turn("Summarise my notes, please.")

    system = session._llm.calls[0][0]["content"]  # type: ignore[attr-defined]
    assert "She keeps a garden." in system and "doc doc" not in system
    events = _events(ws)
    assert next(e for e in events if e["type"] == "reply")["text"] == "Here it is."
    assert not [e for e in events if e["type"] == "citations"]
    await mem.store.close()
