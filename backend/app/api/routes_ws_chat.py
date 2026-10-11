"""Text-chat WebSocket: the text counterpart of `/ws/voice`, without audio.

JSON actions (`open`, `message`, `stop`) in; streamed JSON frames (`token`, `tool`,
`citations`, `state`, `error`, ...) out. `ChatSession` owns the protocol and shares
the voice runtime's LLM client, the memory runtime, the kernel and the persona.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket

from app.api.ws_guard import accept_trusted
from app.services.chat.session import ChatSession

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket) -> None:
    if not await accept_trusted(websocket, "ws/chat"):
        return
    if getattr(websocket.app.state, "chat_store", None) is None:
        # Started without her memory (`app/stores.py`): conversations live in it. Said
        # as a kind the app has words for, and closed; the app tries again now and then.
        await websocket.send_json({"type": "error", "kind": "memory_unavailable"})
        await websocket.close(code=1013)  # try again later
        return
    logger.info("ws/chat connected")
    runtime = websocket.app.state.voice_runtime
    memory = websocket.app.state.memory_runtime
    settings = websocket.app.state.settings
    kernel = websocket.app.state.kernel_runtime
    persona = websocket.app.state.persona
    chat_store = websocket.app.state.chat_store
    project_store = websocket.app.state.project_store
    try:
        await ChatSession(
            websocket, memory, runtime.llm, settings, kernel, persona, chat_store, project_store
        ).run()
    except Exception:
        logger.exception("ws/chat handler error")
        raise
    finally:
        logger.info("ws/chat closed")
