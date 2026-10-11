"""Origin guard for WebSocket handshakes.

CORS does not apply to WebSockets. Without this check, any page the user visits could
open `/ws/chat` and query the memory store, or listen on `/ws/events` for each memory
as it is learned. Every `@router.websocket` handler calls this instead of `accept()`.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

from app.core.origins import is_trusted_origin

logger = logging.getLogger(__name__)

_POLICY_VIOLATION = 1008  # RFC 6455 close code


async def accept_trusted(websocket: WebSocket, lane: str) -> bool:
    """Accept the handshake only from a trusted Origin; else close and return False."""
    origin = websocket.headers.get("origin")
    if not is_trusted_origin(origin):
        logger.warning("refused %s handshake from origin=%r", lane, origin)
        await websocket.close(code=_POLICY_VIOLATION)
        return False
    await websocket.accept()
    return True
