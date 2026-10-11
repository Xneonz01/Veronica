"""App factory + lifespan startup guards."""

from __future__ import annotations

import logging

import pytest

from app.main import create_app, lifespan, websocket_stack_available


def test_websocket_stack_available_true() -> None:
    # uvicorn[standard] is a project dependency, so the WS transport is importable.
    assert websocket_stack_available() is True


async def test_lifespan_fails_loudly_without_ws_stack(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing WebSocket stack fails loudly at startup, not on the first call."""
    monkeypatch.setattr("app.main.websocket_stack_available", lambda: False)
    app = create_app()
    with caplog.at_level(logging.CRITICAL, logger="app.main"):
        with pytest.raises(RuntimeError, match="WebSocket"):
            async with lifespan(app):
                pass
    assert any("WebSocket stack missing" in record.message for record in caplog.records)
