"""What chat says when Ollama refuses in a way asking again can't mend: a model it doesn't
have (bug 30), one that can't use tools, one it couldn't run (bug 45). Each has its own
kind, and the app its own words for it; none of them reads as a passing hiccup."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaModelNoToolsError,
)
from tests.fakes import ScriptedLLM
from tests.test_chat_persistence import _build, _events


class _NotPulled(ScriptedLLM):
    """Ollama doesn't have the model: it answers 404 (`ollama_client`)."""

    async def stream_chat(
        self, messages, tools=None, *, model=None, keep_alive=None, temperature=None
    ):  # type: ignore[no-untyped-def]
        raise OllamaModelMissingError("qwen3.5:9b")
        yield  # pragma: no cover - makes this an async generator, like the real one


async def test_a_chat_model_that_isnt_pulled_is_named_not_called_a_hiccup(
    tmp_path: Path,
) -> None:
    """Bug 30: it read as "Something interrupted that answer", with Ask again, which can
    never help (mutation: map it to turn_failed)."""
    ws, mem, _store, session = await _build(tmp_path, [])
    session._llm = _NotPulled([])  # type: ignore[assignment]

    await session._turn("Gift ideas?")

    error = [e for e in _events(ws) if e["type"] == "error"][-1]
    assert (error["kind"], error["model"]) == ("llm_model_missing", "qwen3.5:9b")
    assert _events(ws)[-1] == {"type": "state", "state": "idle"}
    await mem.store.close()


class _Refusing(ScriptedLLM):
    """Ollama refuses as `ollama_errors` reads it."""

    def __init__(self, refusal: Exception) -> None:
        super().__init__([])
        self._refusal = refusal

    async def stream_chat(
        self, messages, tools=None, *, model=None, keep_alive=None, temperature=None
    ):  # type: ignore[no-untyped-def]
        raise self._refusal
        yield  # pragma: no cover - makes this an async generator, like the real one


@pytest.mark.parametrize(
    ("refusal", "kind"),
    [
        (OllamaModelNoToolsError("smollm:135m"), "llm_no_tools"),
        (
            OllamaModelFailedError("smollm:135m", "llama-server process has terminated"),
            "llm_cant_run",
        ),
    ],
)
async def test_a_refusal_asking_again_cant_mend_is_named(
    tmp_path: Path, refusal: Exception, kind: str
) -> None:
    """Bug 45: both read as "Something interrupted that answer" (mutation: map them to
    turn_failed)."""
    ws, mem, _store, session = await _build(tmp_path, [])
    session._llm = _Refusing(refusal)  # type: ignore[assignment]

    await session._turn("Gift ideas?")

    error = [e for e in _events(ws) if e["type"] == "error"][-1]
    assert (error["kind"], error["model"]) == (kind, "smollm:135m")
    assert _events(ws)[-1] == {"type": "state", "state": "idle"}
    await mem.store.close()
