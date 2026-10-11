"""The largest prompt Veronica sends on each lane, built the way a turn builds it.

Chat: the text persona with the personality at its longest, memory and documents
filled to their budgets, the whole history window and every tool. The call: the
spoken persona, the memory block and the call's tools over a window of spoken turns.
Learning: the extraction request for a part after the first, the largest there is: the
turns read before it that fit, framed as context, and as much of the chat as one
extraction reads beside them (`chat/learning_worker.py`, `learning_sources.py`).
`scripts/prompt_probe.py` sends these to the server for Ollama's own token count, and
`tests/test_llm.py` holds them inside `LlmSettings.num_ctx` with the longest reply beside
them, and the longest persona, tools and budgets inside its floor (`MIN_PROMPT_TOKENS`).

A chat turn is held inside the window by `chat/window.py`, which drops the oldest turns
first and sends back a message too long to fit; the history here is sized like
ordinary turns.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.capabilities import register_capabilities
from app.core.config import Settings
from app.kernel.registry import CapabilityRegistry
from app.services.chat.context import document_fence, documents_header
from app.services.chat.learning_sources import earlier, size, unread
from app.services.chat.learning_worker import transcript_budget
from app.services.chat.store import StoredMessage
from app.services.memory.assembler import render_block
from app.services.memory.extraction import extraction_messages, session_transcript
from app.services.memory.models import (
    Memory,
    MemoryKind,
    RetrievedContext,
    RetrievedMemory,
)
from app.services.persona.defaults import MAX_PERSONALITY_CHARS, build_prompt
from app.services.voice.realtime.workers import build_messages

Prompt = tuple[list[dict[str, Any]], list[dict[str, Any]]]

# Stand-ins sized like the real thing: a remembered fact, a document passage, and the
# turns of a written and of a spoken conversation.
_PERSONALITY = "Warm and plain-spoken, curious about what I'm working on, and honest when unsure. "
_FACT = "Prefers the train to the car for trips under three hours, and books a week ahead."
_PASSAGE = (
    "The boiler should read between 1 and 1.5 bar when cold. If it drops below 1 bar, "
    "top it up with the filling loop under the boiler, then check the radiators for air. "
)
# A chunk's label is its heading path, which the fence cuts at 120 characters.
_LABEL = "house-notes › Heating and hot water › The boiler › "
_ASKED = "Could you help me plan the week? I have the dentist on Thursday and a deadline Friday."
_ANSWERED = (
    "Here's a plan that keeps Friday clear. Monday and Tuesday: the two longest pieces "
    "of the report, in the mornings while you're fresh. Wednesday: review with Morgan "
    "and fix what comes back. Thursday: the dentist at 10:30, so keep the afternoon for "
    "small tasks. Friday: a final read and send it by noon. Want reminders for any of it? "
) * 2
_SAID = "What's a good way to keep basil alive on a windowsill?"
_SPOKEN = (
    "Water it when the top of the soil feels dry, give it the sunniest window you have, "
    "and pinch off the flowers so it keeps growing leaves."
)


def _fill(text: str, chars: int) -> str:
    return (text * (chars // len(text) + 1))[:chars]


def _memory_block(chars: int) -> str:
    """The memory block with its items at the retriever's budget (~4 chars a token)."""
    count = max(1, chars // len(_FACT))
    items = [
        RetrievedMemory(
            memory=Memory(
                id=index,
                kind=MemoryKind.fact,
                content=_FACT,
                normalized_content=_FACT.lower(),
                importance=0.5,
                confidence=1.0,
                pinned=False,
                source="user",
            ),
            score=1.0,
            semantic=1.0,
            lexical=1.0,
        )
        for index in range(1, count + 1)
    ]
    profile = {"name": "Rowan", "nickname": "Ro", "address_by_name": "sparingly"}
    return render_block(RetrievedContext(profile=profile, items=items)) or ""


def _documents(chars: int, chunks: int) -> str:
    """The fenced document block at its budget, split over the chunks retrieval returns."""
    nonce = "0f3a9c21"
    per_chunk = chars // chunks
    fences = [
        document_fence(index, _fill(_LABEL, 120), _fill(_PASSAGE, per_chunk), nonce)
        for index in range(1, chunks + 1)
    ]
    return "\n".join([documents_header(nonce), *fences])


def _history(asked: str, answered: str, turns: int) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for _ in range(turns):
        history += [{"role": "user", "content": asked}, {"role": "assistant", "content": answered}]
    return history


def _backlog(num_ctx: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """A part after the first, cut where the worker cuts it: the turns read before it
    that go with it, and as much of the chat as one extraction reads beside them."""
    budget = transcript_budget(num_ctx)
    history = _history(_ASKED, _ANSWERED, budget // len(_ANSWERED) + 4)
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    messages = [
        StoredMessage(id=index, role=message["role"], content=message["content"], created_at=stamp)
        for index, message in enumerate(history, 1)
    ]
    read, rest = messages[:4], messages[4:]
    before = earlier([(m.role, m.content) for m in read], budget)
    turns, _ = unread(rest, budget - size(before))
    return before, turns


def _tools(settings: Settings, *, call: bool) -> list[dict[str, Any]]:
    """The tool list each lane offers: the call gets only the voice-exposed actions."""
    registry = CapabilityRegistry()
    register_capabilities(registry, settings)
    return registry.schemas(registry.offered(call=call))


def largest(settings: Settings) -> dict[str, Prompt]:
    """Each lane's largest prompt, as messages and the tools sent beside them."""
    personality = _fill(_PERSONALITY, MAX_PERSONALITY_CHARS)
    turns = settings.realtime.history_turns
    memory = _memory_block(settings.memory.token_budget * 4)
    documents = _documents(settings.memory.rag_token_budget * 4, settings.memory.rag_top_k)
    chat = build_messages(
        build_prompt(personality, spoken=False, tools_enabled=True),
        f"{memory}\n\n{documents}",
        _history(_ASKED, _ANSWERED, turns),
        turns,
    )
    call = build_messages(
        build_prompt(personality, spoken=True, tools_enabled=True),
        memory,
        _history(_SAID, _SPOKEN, turns),
        turns,
    )
    before, part = _backlog(settings.llm.num_ctx)
    learning = extraction_messages(session_transcript(part), session_transcript(before))
    return {
        "chat": (chat, _tools(settings, call=False)),
        "call": (call, _tools(settings, call=True)),
        "learning": (learning, []),
    }


def characters(prompt: Prompt) -> int:
    """What a prompt costs in characters: its messages and its tools' schemas."""
    messages, tools = prompt
    text = sum(len(str(message.get("content", ""))) for message in messages)
    return text + len(json.dumps(tools, ensure_ascii=False))
