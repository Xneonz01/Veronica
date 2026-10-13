"""A failure while she acts, writes her activity log or answers in chat is logged by its
type and where it happened, never its message: a message can quote an argument, a file
or what was said, and the console isn't redacted (`core/logging.log_failure`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from app.api import routes_ws_chat
from app.kernel.audit import AuditStore
from app.kernel.capability import CapabilityResult, Decision
from tests.test_chat_persistence import _build, _events
from tests.test_chat_refusals import _Refusing
from tests.test_kernel_preflight import _Args, _kernel, _RecordingAudit, _request

SECRET = "sk-proj-FAKE1111111111111111fake1111111111"


async def test_a_check_before_asking_that_fails_keeps_its_message_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(mutation: log the exception whole)"""

    async def broken(args: _Args) -> CapabilityResult:
        raise RuntimeError(f"could not read {SECRET}")

    kernel, _bus = _kernel(_RecordingAudit(), preflight=broken)

    with caplog.at_level(logging.DEBUG):
        result = await kernel.execute(_request())

    assert result.code == "capability_failed"
    assert "RuntimeError" in caplog.text and SECRET not in caplog.text


async def test_a_chat_turn_that_fails_keeps_its_message_out(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws, memory, _store, session = await _build(tmp_path, [])
    session._llm = _Refusing(RuntimeError(f"stream broke on {SECRET}"))  # type: ignore[assignment]

    with caplog.at_level(logging.DEBUG):
        await session._turn("What was that key again?")

    assert [e for e in _events(ws) if e["type"] == "error"][-1]["kind"] == "turn_failed"
    assert "RuntimeError" in caplog.text and SECRET not in caplog.text
    await memory.store.close()


class _BrokenMemory:
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        raise RuntimeError(f"disk I/O error writing {SECRET}")
        yield  # pragma: no cover - makes this an async generator


async def test_an_activity_log_write_that_fails_keeps_its_message_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = AuditStore(_BrokenMemory())  # type: ignore[arg-type]

    with caplog.at_level(logging.DEBUG):
        await store.record(
            capability="test.write",
            actor="t",
            decision=Decision.failed,
            result=CapabilityResult(ok=False),
            args={"text": SECRET},
            duration_ms=1,
        )

    assert "AUDIT WRITE FAILED" in caplog.text and "RuntimeError" in caplog.text
    assert SECRET not in caplog.text


def test_a_chat_socket_that_fails_is_closed_and_its_message_kept_out(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Raised, the server logged it again with its message (mutation: raise it)."""

    class _Failing:
        def __init__(self, *_args: object) -> None:
            pass

        async def run(self) -> None:
            raise RuntimeError(f"failed on {SECRET}")

    monkeypatch.setattr(routes_ws_chat, "ChatSession", _Failing)
    app = FastAPI()
    app.include_router(routes_ws_chat.router)
    for name in ("voice_runtime", "memory_runtime", "settings", "kernel_runtime"):
        setattr(app.state, name, SimpleNamespace(llm=None))
    for name in ("persona", "chat_store", "project_store"):
        setattr(app.state, name, SimpleNamespace())

    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        with client.websocket_connect("/ws/chat") as socket:
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_text()

    assert closed.value.code == 1011
    assert "RuntimeError" in caplog.text and SECRET not in caplog.text
