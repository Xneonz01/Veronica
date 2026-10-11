"""App-wide event WebSocket: relays non-call bus events to any connected surface.

The per-call `/ws/voice` forwarder only carries that session's events. This lane
lets any surface, with or without an active call, react live to app-wide changes:
voices, memory, documents, confirmation prompts, capability activity, policy and
persona.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_guard import accept_trusted
from app.schemas.events import Event

logger = logging.getLogger(__name__)

router = APIRouter()

# Bus event type -> client wire `type`.
_WIRE: dict[str, str] = {
    "voice.changed": "voice_changed",
    "voices.updated": "voices_updated",
    "memory.updated": "memory_updated",
    "rag.updated": "rag_updated",
    "memory.learned": "memory_learned",
    "memory.learning_mode": "learning_mode",
    # Kernel: confirmation prompts and capability activity.
    "capability.confirm_required": "capability_confirm_required",
    "capability.confirm_expired": "capability_confirm_expired",
    "capability.confirm_resolved": "capability_confirm_resolved",
    "capability.requested": "capability_requested",
    "capability.completed": "capability_completed",
    "capability.denied": "capability_denied",
    "policy.changed": "policy_changed",
    "persona.changed": "persona_changed",
    # The problems /health reports changed after the start: read it again.
    "problems.changed": "problems_changed",
    # A recovery key was made or used: Settings reads its state again.
    "recovery.updated": "recovery_updated",
}


_OUTBOX_MAX = 64  # a stalled tab drops old state, it does not stall the backend


class AppEventForwarder:
    """Bus subscriber that relays the app-wide events to one connected client.

    The handler never awaits the socket. `EventBus.publish` awaits its subscribers and
    is called from the live-call path, so a background tab that stops reading would
    push back through `send_text` into the reply pipeline and stall the call. Events
    are queued and sent by a separate task instead; if the client falls behind, the
    oldest frame is dropped. These are state notifications, and the UI re-queries on
    the next one it receives.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=_OUTBOX_MAX)
        self._task = asyncio.create_task(self._drain())

    async def __call__(self, event: Event) -> None:
        wire = _WIRE.get(event.type)
        if wire is None:
            return
        frame = json.dumps({"type": wire, **event.payload})
        try:
            self._outbox.put_nowait(frame)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                self._outbox.get_nowait()  # shed the oldest, keep the newest
            with suppress(asyncio.QueueFull):
                self._outbox.put_nowait(frame)

    async def _drain(self) -> None:
        while True:
            frame = await self._outbox.get()
            try:
                await self._ws.send_text(frame)
            except Exception as exc:  # a dead socket must not break bus delivery
                logger.debug("app event forward failed: %r", exc)
                return

    async def aclose(self) -> None:
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    if not await accept_trusted(websocket, "ws/events"):
        return
    bus = websocket.app.state.event_bus
    forwarder = AppEventForwarder(websocket)
    unsubs = [bus.subscribe(event_type, forwarder) for event_type in _WIRE]
    logger.info("ws/events connected")
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        for unsub in unsubs:
            unsub()
        await forwarder.aclose()
        logger.info("ws/events closed")
