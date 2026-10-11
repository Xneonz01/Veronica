"""Chat persistence, stop handling, and what the learning loop sees.

Conversations are stored in SQLite and survive a restart. Each turn runs as its own
task, so the receive loop can act on "stop" while a reply is still streaming.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.adapters.llm.ollama_errors import (
    OllamaUnreachableError,
)
from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.core.model_activity import ModelActivity
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.learning_worker import LearningWorker
from app.services.chat.session import ChatSession
from app.services.chat.store import UNTITLED, ChatStore, derive_title
from app.services.memory.extraction import session_transcript
from app.services.memory.models import Extraction
from app.services.memory.runtime import MemoryRuntime
from tests.fakes import (
    FakeLLM,
    FakeWS,
    ScriptedLLM,
    build_fake_chat_store,
    build_fake_kernel_runtime,
    build_fake_memory_runtime,
    build_fake_persona,
    build_fake_project_store,
)


def _events(ws: FakeWS) -> list[dict[str, Any]]:
    return [json.loads(text) for text in ws.sent_text]


async def _build(tmp_path: Path, script: list[dict[str, Any]], store: ChatStore | None = None):
    ws = FakeWS()
    mem = await build_fake_memory_runtime(tmp_path / "db", InProcessEventBus())
    kernel = await build_fake_kernel_runtime(mem, InProcessEventBus())
    chat_store = store or await build_fake_chat_store(mem.store)
    session = ChatSession(
        ws,
        mem,
        ScriptedLLM(script),
        get_settings(),
        kernel,
        build_fake_persona(tmp_path),
        chat_store,
        await build_fake_project_store(mem.store),
    )  # type: ignore[arg-type]
    return ws, mem, chat_store, session


# -- titles --------------------------------------------------------------------


def test_a_conversation_names_itself_from_its_first_line() -> None:
    assert derive_title("  what's  the  weather  ") == "what's the weather"
    assert derive_title("") == UNTITLED
    long_title = derive_title("x" * 40 + " " + "y" * 40)
    assert len(long_title) <= 61 and long_title.endswith("…")


# -- persistence ---------------------------------------------------------------


async def test_a_conversation_survives_a_restart(tmp_path: Path) -> None:
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["The kettle is on."]}])
    await session._turn("put the kettle on")

    conversations = await store.list()
    assert len(conversations) == 1
    assert conversations[0].title == "put the kettle on"
    assert conversations[0].message_count == 2  # the question and the answer

    # A new session on the same store is the "restart": nothing is carried in memory.
    ws2, _mem2, _store2, session2 = await _build(tmp_path, [{"tokens": ["ok"]}], store=store)
    await session2._open(conversations[0].id)

    opened = next(e for e in _events(ws2) if e["type"] == "opened")
    assert [m["content"] for m in opened["messages"]] == [
        "put the kettle on",
        "The kettle is on.",
    ]
    # The replayed turns are back in the model's context, so the conversation can continue.
    assert session2._history == [
        {"role": "user", "content": "put the kettle on"},
        {"role": "assistant", "content": "The kettle is on."},
    ]
    await mem.store.close()


async def test_a_tool_call_is_recorded_but_not_replayed_into_context(tmp_path: Path) -> None:
    """Tool rows are stored for the transcript but not replayed into context.

    Without their call ids they would only confuse the model on the next turn.
    """
    ws, mem, store, session = await _build(
        tmp_path,
        [
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "memory_save",
                            "arguments": {"content": "User likes tea", "kind": "preference"},
                        }
                    }
                ]
            },
            {"tokens": ["Noted."]},
        ],
    )
    await session._turn("remember I like tea")
    conversation_id = session._conversation_id
    assert conversation_id is not None

    stored = await store.messages(conversation_id)
    roles = [m.role for m in stored]
    assert roles == ["user", "tool", "assistant"]
    assert stored[1].tool_name == "memory_save" and stored[1].tool_ok is True

    _ws2, _mem2, _store2, session2 = await _build(tmp_path, [{"tokens": ["ok"]}], store=store)
    await session2._open(conversation_id)
    assert [m["role"] for m in session2._history] == ["user", "assistant"]
    await mem.store.close()


async def test_renaming_and_deleting_a_conversation(tmp_path: Path) -> None:
    _ws, mem, store, session = await _build(tmp_path, [{"tokens": ["hi"]}])
    await session._turn("hello")
    conversation_id = session._conversation_id
    assert conversation_id is not None

    assert await store.rename(conversation_id, "  Kitchen   plans  ") is True
    assert (await store.get(conversation_id)).title == "Kitchen plans"  # type: ignore[union-attr]

    assert await store.delete(conversation_id) is True
    assert await store.get(conversation_id) is None
    # The messages go with it: an orphan would still be counted by the listing query.
    assert await store.messages(conversation_id) == []
    assert await store.delete(conversation_id) is False  # already gone, not an error
    await mem.store.close()


async def test_opening_a_conversation_that_is_gone_is_a_calm_message(tmp_path: Path) -> None:
    ws, mem, _store, session = await _build(tmp_path, [{"tokens": ["hi"]}])
    await session._open(4242)

    error = next(e for e in _events(ws) if e["type"] == "error")
    assert error["message"] == "That conversation is no longer here."
    assert "4242" not in error["message"]
    await mem.store.close()


# -- stop ----------------------------------------------------------------------


async def test_stop_cancels_the_turn_and_commits_nothing(tmp_path: Path) -> None:
    """A stopped reply is not stored.

    Otherwise a truncated sentence would be replayed as the answer on reload.
    """
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["I am thinking"], "hang": True}])

    await session._dispatch({"action": "message", "text": "take your time"})
    await asyncio.sleep(0.05)  # let the turn start streaming
    assert session._turn_task is not None and not session._turn_task.done()

    await session._dispatch({"action": "stop"})
    assert session._turn_task is None

    conversation_id = [e for e in _events(ws) if e["type"] == "conversation"][0]["conversation_id"]
    roles = [m.role for m in await store.messages(conversation_id)]
    assert roles == ["user"]  # the question stands; no partial answer was stored
    # The stopped turn says idle once, then Stop is acknowledged once, after it.
    tail = _events(ws)[-2:]
    assert tail == [{"type": "state", "state": "idle"}, {"type": "stopped"}]
    await mem.store.close()


async def test_stop_with_nothing_running_is_still_acknowledged(tmp_path: Path) -> None:
    ws, mem, _store, session = await _build(tmp_path, [{"tokens": ["Done."]}])
    await session._turn("hello")
    await session._dispatch({"action": "stop"})
    events = _events(ws)
    assert events[-1] == {"type": "stopped"}
    assert [e for e in events if e["type"] == "reply"]  # the finished answer stands
    await mem.store.close()


async def test_blank_text_releases_the_composer(tmp_path: Path) -> None:
    ws, mem, _store, session = await _build(tmp_path, [{"tokens": ["unused"]}])
    await session._turn("\x85\x1c ")  # whitespace to Python, not to JavaScript
    assert _events(ws) == [{"type": "state", "state": "idle"}]
    await mem.store.close()


async def test_a_conversation_that_is_gone_leaves_the_previous_one(tmp_path: Path) -> None:
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["a"]}, {"tokens": ["b"]}])
    await session._turn("first conversation")
    first = session._conversation_id
    await session._open(99999)
    assert [e for e in _events(ws) if e["type"] == "error"][-1]["kind"] == "bad_conversation"
    await session._turn("where does this go?")
    assert session._conversation_id not in (None, first)  # a new one, not the old one
    assert [m.role for m in await store.messages(first)] == ["user", "assistant"]  # type: ignore[arg-type]
    await mem.store.close()


async def test_a_second_message_while_streaming_is_refused_not_interleaved(
    tmp_path: Path,
) -> None:
    ws, mem, _store, session = await _build(tmp_path, [{"tokens": ["one"], "hang": True}])

    await session._dispatch({"action": "message", "text": "first"})
    await asyncio.sleep(0.05)
    first_task = session._turn_task
    await session._dispatch({"action": "message", "text": "second"})

    assert session._turn_task is first_task  # the original turn was not replaced
    assert any(e.get("kind") == "busy" for e in _events(ws))
    await session._cancel_turn()
    await mem.store.close()


async def _learned(mem: MemoryRuntime, store: ChatStore) -> list[str]:
    """Let the learning worker read everything due; the transcripts the extractor got."""
    captured: list[str] = []

    async def capture(history: list[dict[str, Any]], *_args: object) -> Extraction:
        captured.append(session_transcript(history))
        return Extraction()

    mem.learning.extract = capture  # type: ignore[assignment,method-assign]
    worker = LearningWorker(
        LearningQueue(mem.store),
        store,
        mem.learning,
        ScriptedLLM([]),
        ModelActivity(),
        calls_live=lambda: 0,
        model="m",
        quiet_s=0,
        max_chars=10_000,
    )
    while await worker.step():
        pass
    return captured


async def test_reopening_does_not_re_learn_the_whole_conversation(tmp_path: Path) -> None:
    """Once learned from, a conversation's turns are not read again when it is reopened
    and continued; the model still sees them all."""
    _ws, mem, store, session = await _build(tmp_path, [{"tokens": ["Noted."]}])
    await session._turn("I live in Leeds")
    conversation_id = session._conversation_id
    assert conversation_id is not None
    assert "Leeds" in (await _learned(mem, store))[0]

    _ws2, _mem2, _store2, session2 = await _build(tmp_path, [{"tokens": ["Sure."]}], store=store)
    await session2._open(conversation_id)
    await session2._turn("what is the weather")

    assert len(session2._history) == 4  # the model still sees the whole conversation
    captured = await _learned(mem, store)
    assert len(captured) == 1
    assert "Leeds" not in captured[0]  # the replayed turn is not re-extracted
    assert "what is the weather" in captured[0]
    await mem.store.close()


async def test_a_turn_that_fails_before_the_model_still_releases_the_surface(
    tmp_path: Path,
) -> None:
    """A turn that fails before reaching the model still returns the surface to idle.

    A turn runs as a bare task, so an exception outside its guard would reach nobody
    and leave the composer stuck on "Thinking".
    """
    _ws, mem, store, session = await _build(tmp_path, [{"tokens": ["hi"]}])

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database is gone")

    session._store = store  # type: ignore[assignment]
    store.create = boom  # type: ignore[assignment,method-assign]

    await session._turn("hello")

    states = [e for e in _events(session._ws) if e["type"] == "state"]  # type: ignore[attr-defined]
    assert states[-1] == {"type": "state", "state": "idle"}  # released, not hung
    assert any(e["type"] == "error" for e in _events(session._ws))  # type: ignore[attr-defined]
    await mem.store.close()


async def test_a_document_grounded_reply_is_not_learned_as_a_fact_about_the_user(
    tmp_path: Path,
) -> None:
    """A reply quoting an uploaded document is not something the user said.

    Cited turns are left out of the learning transcript; otherwise the extractor
    reads document content as the user's own statements and stores it as personal
    memories.
    """
    _ws, mem, store, session = await _build(
        tmp_path, [{"tokens": ["Connect the circuit carefully [1]."]}]
    )
    await mem.rag.index_text(
        "Sourdough Guide", "Sourdough starter dough. Feed the starter carefully."
    )

    await session._turn("how do I feed the sourdough starter?")

    captured = await _learned(mem, store)
    assert captured, "learning did not run"
    assert "Connect the circuit carefully" not in captured[0]  # the document stayed out
    assert "how do I feed the sourdough starter?" in captured[0]  # the user's own words remain
    await mem.store.close()


async def test_an_ordinary_reply_is_still_learned_from(tmp_path: Path) -> None:
    """Only cited turns are excluded; an ordinary exchange still reaches the extractor."""
    _ws, mem, store, session = await _build(tmp_path, [{"tokens": ["Noted."]}])
    await session._turn("I live in Leeds")

    captured = await _learned(mem, store)

    assert captured and "I live in Leeds" in captured[0] and "Noted." in captured[0]
    await mem.store.close()


async def test_a_conversation_switched_away_from_is_still_learned_from(tmp_path: Path) -> None:
    """Bug 32: opening another conversation replaced the session's history, so only the
    one open when the socket closed was learned from. Nothing here closes it."""
    _ws, mem, store, session = await _build(tmp_path, [{"tokens": ["a"]}, {"tokens": ["b"]}])
    await session._turn("the first conversation")
    await session._open(None)  # the "New" button
    await session._turn("something worth remembering")

    captured = await _learned(mem, store)

    assert len(captured) == 2
    assert "the first conversation" in captured[0]
    assert "something worth remembering" in captured[1]
    assert "the first conversation" not in captured[1]
    await mem.store.close()


async def test_a_fact_said_in_chat_reaches_memory_through_the_queue(tmp_path: Path) -> None:
    """End to end: the turn, the queue, the worker and the real extractor. When the
    worker runs is `test_learning_worker.py`'s business; this runs it at once."""
    _ws, mem, store, session = await _build(tmp_path, [{"tokens": ["Lovely."]}])
    await session._turn("My cat is called Max")
    extractor = FakeLLM(
        [
            json.dumps(
                {
                    "profile": {},
                    "facts": [{"content": "User has a cat named Max", "confidence": 0.95}],
                }
            )
        ]
    )
    worker = LearningWorker(
        LearningQueue(mem.store),
        store,
        mem.learning,
        extractor,
        ModelActivity(),
        calls_live=lambda: 0,
        model="m",
        quiet_s=0,
        max_chars=10_000,
    )

    assert await worker.step() is True

    assert [m.content for m in await mem.service.list()] == ["User has a cat named Max"]
    assert await worker.step() is False  # caught up
    await mem.store.close()


async def test_a_long_conversation_reopens_at_its_most_recent_turns(tmp_path: Path) -> None:
    """A conversation past the message limit reopens at its newest turns.

    A plain `ORDER BY id LIMIT ?` returns the oldest rows, which would show the
    beginning of the conversation and rebuild the model's context from it.
    """
    _ws, mem, store, session = await _build(tmp_path, [{"tokens": ["ok"]}])
    conversation = await store.create("a long one")
    for index in range(520):
        await store.add_message(
            conversation.id, "user" if index % 2 == 0 else "assistant", f"m{index}"
        )

    rows = await store.messages(conversation.id)
    assert len(rows) == 500
    assert rows[-1].content == "m519"  # the newest turn is present
    assert rows[0].content == "m20"  # the oldest 20 are what got dropped
    assert [r.content for r in rows] == sorted(
        (r.content for r in rows), key=lambda c: int(c[1:])
    )  # still oldest-first for display

    await session._open(conversation.id)
    assert session._history[-1]["content"] == "m519"
    await mem.store.close()


class _DownThenUp(ScriptedLLM):
    """The model is unreachable on the first call, then answers."""

    async def stream_chat(
        self, messages, tools=None, *, model=None, keep_alive=None, temperature=None
    ):  # type: ignore[no-untyped-def]
        if not self.calls:
            self.calls.append(list(messages))
            raise OllamaUnreachableError("ollama down")
        async for chunk in super().stream_chat(messages, tools, model=model, keep_alive=keep_alive):
            yield chunk


async def test_asking_again_after_a_failure_keeps_one_copy_of_the_question(
    tmp_path: Path,
) -> None:
    ws, mem, store, session = await _build(tmp_path, [])
    session._llm = _DownThenUp([{"tokens": ["Try a good book."]}])  # type: ignore[assignment]
    await session._turn("Gift ideas?")
    assert [e for e in _events(ws) if e["type"] == "error"][-1]["kind"] == "llm_unreachable"

    await session._turn("Gift ideas?", again=True)
    conversation_id = session._conversation_id
    assert conversation_id is not None
    stored = [(m.role, m.content) for m in await store.messages(conversation_id)]
    assert stored == [("user", "Gift ideas?"), ("assistant", "Try a good book.")]
    assert [m["role"] for m in session._history] == ["user", "assistant"]
    await mem.store.close()


async def test_asking_again_for_a_question_never_saved_saves_it(tmp_path: Path) -> None:
    # The failure came before the question was stored: asking again is a first ask.
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["Hello."]}])
    await session._turn("Hi there", again=True)
    conversation_id = session._conversation_id
    assert conversation_id is not None
    roles = [m.role for m in await store.messages(conversation_id)]
    assert roles == ["user", "assistant"]
    await mem.store.close()


async def test_a_message_too_long_to_read_is_refused_and_nothing_kept(tmp_path: Path) -> None:
    """Sent as it was, the model's window would have dropped the persona and the tool
    rules to fit it; saved, every later window would stop at it and lose all that
    came before (mutations: send it anyway; save it before the check)."""
    ws, mem, store, session = await _build(tmp_path, [{"tokens": ["read it"]}])

    await session._turn("word " * 8000)

    errors = [e for e in _events(ws) if e["type"] == "error"]
    assert [e["kind"] for e in errors] == ["message_too_long"]
    assert _events(ws)[-1] == {"type": "state", "state": "idle"}
    assert session._conversation_id is None and session._history == []
    assert session._llm.calls == []  # type: ignore[attr-defined]
    assert await store.list() == []
    await mem.store.close()
