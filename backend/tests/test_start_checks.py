"""What a start checks and prepares once the stores are open (`app/startup.py`): a model
that isn't on disk becomes a problem the app has words for, and the memory model is
loaded ahead of the first message, but never for a memory she started without or a
model that isn't there. Bug 31: the first message waited about 5 s for that load."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters import model_files
from app.adapters.embeddings.e5_embedder import E5Embedder
from app.core.config import get_settings
from app.main import create_app
from app.services.memory.migrations import LATEST
from tests.test_degraded_start import _at_version


@contextmanager
def started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, warm: bool) -> Iterator[TestClient]:
    monkeypatch.setenv("VERONICA_MEMORY__DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("VERONICA_MEMORY__WARM_ON_START", "true" if warm else "false")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(), base_url="http://localhost") as client:
            yield client
    finally:
        get_settings.cache_clear()


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the start asked the memory model; nothing is really loaded."""
    asked: list[str] = []

    async def embed(_embedder: E5Embedder, texts: list[str]) -> list[list[float]]:
        asked.extend(texts)
        return []

    monkeypatch.setattr(E5Embedder, "embed", embed)
    return asked


def _absent(*codes: str) -> list[model_files.Weights]:
    return [model_files.Weights(code, code, code, lambda: None) for code in codes]


def test_the_memory_model_is_loaded_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: list[str]
) -> None:
    """(mutation: no warm-up)"""
    with started(tmp_path, monkeypatch, warm=True):
        pass  # leaving waits for the start's background work

    assert loads == ["warm up"]  # loaded, and run once


def test_the_warm_up_runs_beside_the_start_not_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting takes as long as it did (mutation: await the warm-up before serving)."""
    release = threading.Event()
    progress: list[str] = []

    async def slow(_embedder: E5Embedder, _texts: list[str]) -> list[list[float]]:
        progress.append("started")
        await asyncio.to_thread(release.wait, 5)
        progress.append("finished")
        return []

    monkeypatch.setattr(E5Embedder, "embed", slow)

    with started(tmp_path, monkeypatch, warm=True) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert "finished" not in progress  # she answered while it was still loading
        release.set()

    assert progress == ["started", "finished"]  # shutdown waited for it


def test_other_missing_models_leave_the_warm_up_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: list[str]
) -> None:
    """Only the memory model's own absence stops it (mutation: skip it for any)."""
    monkeypatch.setattr(model_files, "missing", lambda _settings: _absent("tts"))

    with started(tmp_path, monkeypatch, warm=True):
        pass

    assert loads == ["warm up"]


def test_nothing_is_loaded_when_the_warm_up_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: list[str]
) -> None:
    with started(tmp_path, monkeypatch, warm=False):
        pass

    assert loads == []


def test_a_memory_she_started_without_is_not_loaded_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: list[str]
) -> None:
    """Nothing would use it (mutation: warm whatever the stores did)."""
    _at_version(tmp_path / "memory.db", LATEST + 1)

    with started(tmp_path, monkeypatch, warm=True) as client:
        assert client.get("/health").json()["status"] == "degraded"

    assert loads == []


def test_missing_models_are_a_problem_and_a_missing_memory_model_isnt_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: list[str]
) -> None:
    """Said by their codes, which the app has words for (mutations: log them only; warm
    a model that isn't there)."""
    monkeypatch.setattr(model_files, "missing", lambda _settings: _absent("tts", "embedding"))

    with started(tmp_path, monkeypatch, warm=True) as client:
        body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["problems"] == [{"code": "models_missing", "detail": {"models": "tts,embedding"}}]
    assert loads == []


def test_a_warm_up_that_fails_is_logged_and_leaves_the_start_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The first use reports it, as before the warm-up existed (mutation: let the task's
    error go unhandled, which asyncio only reports when it is collected)."""

    async def broken(_embedder: E5Embedder, _texts: list[str]) -> list[list[float]]:
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(E5Embedder, "embed", broken)

    with caplog.at_level(logging.ERROR, logger="app.startup"):
        with started(tmp_path, monkeypatch, warm=True) as client:
            body: dict[str, Any] = client.get("/health").json()

    assert (body["status"], body["problems"]) == ("ok", [])
    assert "could not load the memory model at start" in caplog.text
