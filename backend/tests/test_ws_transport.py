"""Real WebSocket transport test for /ws/voice.

The FakeWS orchestration tests never open a socket, so a broken handshake, an
unregistered route, or a crash on connect would pass them all. This test opens a
real WebSocket through Starlette's TestClient against the full application
(create_app, lifespan, router, RealtimeSession, event bus, forwarder), with only
the model-bearing adapters faked, so a transport or wiring break fails here.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.voice.realtime import lifecycle
from app.services.voice.realtime.session import RealtimeSession
from app.services.voice.turn import TurnEvent
from tests.fakes import FakeLLM, FakeSTT, FakeTTS, build_fake_runtime


class _SilentDetector:
    """Never fires — keeps warmup off the real Silero VAD model."""

    def process_audio(self, _samples: Any) -> list[TurnEvent]:
        return []


async def _fake_build_detector(_s: RealtimeSession) -> _SilentDetector:
    return _SilentDetector()


def test_ws_voice_handshake_and_call_start(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep warmup from loading the real VAD model (warmup builds the detector through
    # `lifecycle.build_detector`); the rest is faked via the runtime.
    monkeypatch.setattr(lifecycle, "build_detector", _fake_build_detector)

    app = create_app()
    with TestClient(app, base_url="http://localhost") as client:
        # Lifespan has run; swap real adapters for fakes so no model/network loads.
        app.state.voice_runtime = build_fake_runtime(
            stt=FakeSTT([], None),
            tts=FakeTTS(),
            llm=FakeLLM(["ok"]),
        )
        # Entering the context performs the handshake; a non-101 upgrade raises.
        with client.websocket_connect("ws://localhost/ws/voice") as ws:
            # Greeting off so the handshake sequence is just connecting -> warming_up -> listening.
            ws.send_json({"action": "call_start", "greeting": {"enabled": False}})

            seen: list[str] = []
            for _ in range(3):
                msg = ws.receive_json()
                assert msg["type"] == "veronica_state"
                seen.append(msg["state"])
            # connecting (instant ack) -> warming_up (cold load) -> listening (live)
            assert seen == ["connecting", "warming_up", "listening"]
