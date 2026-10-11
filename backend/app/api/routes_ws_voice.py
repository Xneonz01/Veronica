"""Voice WebSockets.

`/ws/voice` runs the realtime hands-free call (RealtimeSession); `/ws/voice/harness`
is a push-to-talk harness for debugging STT and TTS in isolation. Audio is binary
(16 kHz PCM16 in, 24 kHz PCM16 out); control, transcripts and state are JSON.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, WebSocket

from app.api.ws_guard import accept_trusted
from app.services.persona import Surface
from app.services.tools import ToolBroker
from app.services.voice.harness import HarnessSession
from app.services.voice.realtime.session import RealtimeSession
from app.services.voice.runtime import VoiceRuntime

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    if not await accept_trusted(websocket, "ws/voice"):
        return
    conn_id = uuid4().hex
    logger.info("ws/voice connected | conn=%s", conn_id)
    runtime: VoiceRuntime = websocket.app.state.voice_runtime
    bus = websocket.app.state.event_bus
    settings = websocket.app.state.settings
    memory = getattr(websocket.app.state, "memory_runtime", None)
    kernel = getattr(websocket.app.state, "kernel_runtime", None)
    # Calls only get capabilities marked `voice_exposed` (`voice_only`), and use their
    # own actor so the audit log shows which surface acted.
    tools = (
        ToolBroker(kernel.kernel, kernel.registry, actor="veronica:voice", voice_only=True)
        if kernel is not None and memory is not None
        else None
    )
    session = RealtimeSession(
        websocket,
        runtime,
        bus,
        settings,
        # The tool rules (tool output is data, not instructions; summarise results
        # aloud) are only part of the prompt when tools are actually offered.
        persona=websocket.app.state.persona.provider(
            Surface.voice, tools_enabled=tools is not None
        ),
        conn_id=conn_id,
        memory=memory,
        tools=tools,
    )
    if memory is not None:  # wire the assembler as the per-turn context prefetch
        session.prefetch = memory.assembler.assemble
    try:
        await session.run()
    except Exception:
        # The handshake succeeded but the call crashed; log it rather than fail silently.
        logger.exception("ws/voice handler error | conn=%s", conn_id)
        raise
    finally:
        logger.info("ws/voice closed | conn=%s", conn_id)


@router.websocket("/ws/voice/harness")
async def voice_harness_ws(websocket: WebSocket) -> None:
    if not await accept_trusted(websocket, "ws/voice/harness"):
        return
    runtime: VoiceRuntime = websocket.app.state.voice_runtime
    session = HarnessSession(
        websocket, runtime.stt, runtime.tts, runtime.voice_service.active_voice_id
    )
    await session.run()
