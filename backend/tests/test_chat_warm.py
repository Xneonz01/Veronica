"""The chat model is loaded at start, beside the memory model.

Nothing else loaded it before the first message or a call: a first message sent at
launch waited 21 s, 16 of them for the model, and chat seemed to need a call first,
because a call's warm-up loaded it (found in use of 0.35.0). Only when the card has
room for it, and a quit never waits for the load.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import startup
from app.adapters.llm.ollama_client import OllamaLLMClient
from app.adapters.llm.ollama_errors import OllamaStreamError, OllamaUnreachableError
from app.adapters.llm.types import LlmChunk
from app.core import machine
from app.core.config import get_settings
from app.main import create_app
from app.services.voice.realtime import card_room


@contextmanager
def _started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, warm: bool) -> Iterator[None]:
    monkeypatch.setenv("VERONICA_MEMORY__DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("VERONICA_LLM__WARM_ON_START", "true" if warm else "false")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(), base_url="http://localhost"):
            yield
    finally:
        get_settings.cache_clear()


def _room(monkeypatch: pytest.MonkeyPatch, fits: bool) -> None:
    async def fits_her_model(_settings: Any) -> bool:
        return fits

    monkeypatch.setattr(card_room, "fits_her_model", fits_her_model)


@pytest.fixture
def warmed(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """The models the start asked Ollama to load; nothing is really loaded."""
    asked: list[str | None] = []

    async def warm(_llm: OllamaLLMClient, model: str | None = None) -> None:
        asked.append(model)

    monkeypatch.setattr(OllamaLLMClient, "warm", warm)
    return asked


def _until(done: Any, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_the_chat_model_is_loaded_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warmed: list[str | None]
) -> None:
    """The model in Settings, once (mutation: no warm-up at start)."""
    _room(monkeypatch, fits=True)
    with _started(tmp_path, monkeypatch, warm=True):
        _until(lambda: warmed)
        model = get_settings().llm.model

    assert warmed == [model]


@pytest.mark.parametrize(("warm", "fits"), [(False, True), (True, False)])
def test_turned_off_or_without_room_it_waits_for_the_first_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warmed: list[str | None],
    warm: bool,
    fits: bool,
) -> None:
    """Another program may be using the card (mutations: warm whatever the setting;
    warm without room)."""
    _room(monkeypatch, fits=fits)
    with _started(tmp_path, monkeypatch, warm=warm):
        time.sleep(0.2)

    assert warmed == []


def test_a_quit_doesnt_wait_for_the_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cold load takes longer than the 10 s shutdown gives its work: a quit soon after
    launch waited all of it (mutation: wait for the warm-up like any work)."""
    _room(monkeypatch, fits=True)
    began: list[bool] = []

    async def warm(_llm: OllamaLLMClient, _model: str | None = None) -> None:
        began.append(True)
        await asyncio.sleep(60)

    monkeypatch.setattr(OllamaLLMClient, "warm", warm)
    with _started(tmp_path, monkeypatch, warm=True):
        _until(lambda: began)
        quitting = time.monotonic()

    assert began and time.monotonic() - quitting < 5


async def test_a_model_that_cant_be_reached_never_stops_the_start(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Left for the first message to report; the log says what kind, never the message
    (mutation: let the failure through)."""
    _room(monkeypatch, fits=True)

    class _Down:
        async def warm(self, _model: str | None = None) -> None:
            raise OllamaUnreachableError("http://127.0.0.1:11434 refused, key sk-proj-FAKE1")

    with caplog.at_level(logging.WARNING):
        await startup.warm_chat_model(_Down(), get_settings())  # type: ignore[arg-type]

    assert "OllamaUnreachableError" in caplog.text and "FAKE1" not in caplog.text


async def test_any_other_failure_is_logged_not_left_in_the_task(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream error or an HTTP one sat unseen in the task until a quit dropped it
    (mutation: catch only Ollama's three)."""
    _room(monkeypatch, fits=True)

    class _Broken:
        async def warm(self, _model: str | None = None) -> None:
            raise OllamaStreamError("the model stream ended, key sk-proj-FAKE2")

    with caplog.at_level(logging.WARNING):
        await startup.warm_chat_model(_Broken(), get_settings())  # type: ignore[arg-type]

    assert "OllamaStreamError" in caplog.text and "FAKE2" not in caplog.text


async def test_warming_asks_one_short_question_and_closes_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Left open, the stream held the model's activity mark and Ollama kept writing
    (mutations: read the whole answer; leave the stream to the collector)."""
    seen: list[dict[str, Any]] = []
    read: list[int] = []
    closed: list[bool] = []

    async def stream_chat(
        _llm: OllamaLLMClient, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any
    ) -> AsyncIterator[LlmChunk]:
        seen.append({"messages": messages, "tools": tools, **kwargs})
        try:
            for index in range(3):
                read.append(index)
                yield LlmChunk(content="x")
        finally:
            closed.append(True)

    monkeypatch.setattr(OllamaLLMClient, "stream_chat", stream_chat)
    llm = OllamaLLMClient(get_settings().llm)

    await llm.warm("m")

    assert seen == [
        {"messages": [{"role": "user", "content": "Ready"}], "tools": None, "model": "m"}
    ]
    assert read == [0] and closed == [True]
    await llm.aclose()


@pytest.mark.parametrize(
    ("free_mib", "loaded", "fits"),
    [
        (23_000, False, True),  # room for 17,000 of files, 1,536 of memory model and 384
        (18_000, False, False),  # 17,000 + 1,536 + 384 is 18,920: not
        (1_000, True, True),  # loaded already: nothing to add
    ],
)
async def test_room_for_her_model_is_what_the_card_has_free(
    monkeypatch: pytest.MonkeyPatch, free_mib: int, loaded: bool, fits: bool
) -> None:
    """Her model's files, the memory model loading beside it, and the room a call keeps
    (mutations: never check; forget the memory model)."""
    monkeypatch.setattr(machine, "card_use", lambda: machine.CardUse(32_000 - free_mib, 32_000))
    monkeypatch.setattr(card_room, "her_model", lambda _llm: card_room.HerModel(17_000, loaded))
    monkeypatch.setattr(card_room, "_all_on_the_card", lambda _settings: True)

    assert await card_room.fits_her_model(get_settings()) is fits


async def test_a_card_that_cant_be_read_lets_the_load_go_ahead(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The first message would load it anyway; said in a line, as a call's check says it,
    not a traceback at every start (mutations: skip it on a failed reading; trace it)."""

    def broken() -> None:
        raise machine.ReadingError("no driver")

    monkeypatch.setattr(machine, "card_use", broken)
    monkeypatch.setattr(card_room, "_all_on_the_card", lambda _settings: True)

    with caplog.at_level(logging.WARNING):
        assert await card_room.fits_her_model(get_settings()) is True
    assert "couldn't be read" in caplog.text and "Traceback" not in caplog.text
