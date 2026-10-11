"""A search that fails leaves the turn standing (bug 25): she answers from the rest, and
the answer says what it went without. One failed search used to fail the whole turn."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.services.chat import context
from tests.test_claim_caveats import _chat, _reply

QUESTION = "how do I feed the sourdough starter?"


class _Box:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(self, _level: str, event: str, **fields: object) -> None:
        self.events.append((event, fields))


async def _broken(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("CUDA out of memory while reading: the vault code is 4411")


async def test_a_failed_document_search_leaves_an_answer_that_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: let the error fail the turn; say nothing of it)"""
    box = _Box()
    monkeypatch.setattr(context, "current_blackbox", lambda: box)
    memory, session, ws = await _chat(tmp_path, [{"tokens": ["Feed it daily."]}])
    await memory.rag.index_text("Sourdough Guide", "Sourdough starter. Feed it carefully.")
    monkeypatch.setattr(memory.rag, "retrieve", _broken)

    await session._turn(QUESTION)

    missed = [{"family": "missed_documents", "text": context.MISSED["documents"]}]
    assert _reply(ws) == {"type": "reply", "text": "Feed it daily.", "caveats": missed}
    assert session._conversation_id is not None
    stored = await session._store.messages(session._conversation_id)
    assert stored[-1].caveats == missed
    # The kind of failure, never what it said: an error can quote what it was reading.
    assert box.events == [("chat.search_failed", {"part": "documents", "error": "RuntimeError"})]
    await memory.store.close()


async def test_a_failed_memory_search_still_reads_the_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: one guard around both searches)"""
    memory, session, ws = await _chat(tmp_path, [{"tokens": ["Feed the starter [1]."]}])
    await memory.rag.index_text(
        "Sourdough Guide", "Sourdough starter dough. Feed the starter carefully."
    )

    async def locked(*_args: Any) -> str:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(memory.assembler, "assemble", locked)

    await session._turn(QUESTION)

    frames = [json.loads(text) for text in ws.sent_text]
    assert _reply(ws)["caveats"] == [{"family": "missed_memory", "text": context.MISSED["memory"]}]
    assert [f["sources"][0]["title"] for f in frames if f["type"] == "citations"] == [
        "Sourdough Guide"
    ]
    await memory.store.close()


async def test_someone_with_no_documents_is_told_nothing_about_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(mutation: say it whenever the search fails)"""
    memory, session, ws = await _chat(tmp_path, [{"tokens": ["Feed it daily."]}])
    monkeypatch.setattr(memory.rag, "retrieve", _broken)

    await session._turn(QUESTION)

    assert _reply(ws)["caveats"] == []
    await memory.store.close()


async def test_a_project_is_judged_by_its_own_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a project, its documents are the ones searched (mutation: every document)."""
    memory, _session, _ws = await _chat(tmp_path, [])
    await memory.rag.index_text("Sourdough Guide", "Sourdough starter. Feed it carefully.")
    monkeypatch.setattr(memory.rag, "retrieve", _broken)

    empty = await context.assemble(memory, _session._settings, QUESTION, document_ids=[])
    held = await context.assemble(memory, _session._settings, QUESTION, document_ids=[1])

    assert empty.missed == () and held.missed == ("documents",)
    await memory.store.close()


async def test_a_stop_during_a_search_still_ends_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stop isn't a failed search: nothing is answered (mutation: catch BaseException)."""
    memory, session, ws = await _chat(tmp_path, [{"tokens": ["Feed it daily."]}])

    async def stopped(*_args: Any, **_kwargs: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(memory.rag, "retrieve", stopped)

    with pytest.raises(asyncio.CancelledError):
        await session._turn(QUESTION)

    assert not [text for text in ws.sent_text if json.loads(text)["type"] == "reply"]
    await memory.store.close()
