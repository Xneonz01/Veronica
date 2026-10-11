"""Refuse requests addressed to any host name other than this machine's own.

DNS rebinding is the threat. A page on `evil.example` re-points that name at
127.0.0.1, and the browser then talks to this server while treating it as the page's
own origin, so every same-origin rule works for the attacker. The origin checks
can't see it, because a same-origin GET carries no Origin at all. The one thing the
page can't hide is the name it used, which arrives in the Host header.

Pure ASGI, so it covers the WebSocket handshakes as well as HTTP: an
`@app.middleware("http")` never sees a WebSocket. It matches the host name and
ignores the port, which the same client chooses. An absent Host (HTTP/1.0, raw
sockets) is let through: browsers always send one.
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Names that always mean this machine and that nobody else's DNS can point here.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})

_POLICY_VIOLATION = 1008  # RFC 6455 close code; Starlette answers it with a 403


def host_of(raw: str) -> str:
    """The host name in a Host header, lower-cased, without the port.

    IPv6 keeps its brackets (`[::1]:8000` -> `[::1]`); splitting on ":" would cut it
    apart and refuse every IPv6 caller.
    """
    value = raw.strip().lower()
    if value.startswith("["):
        closing = value.find("]")
        return value if closing == -1 else value[: closing + 1]
    return value.split(":", 1)[0]


class HostGuard:
    """ASGI middleware: answer only requests addressed to a loopback name."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            raw = _host_header(scope)
            if raw is not None and host_of(raw) not in LOOPBACK_HOSTS:
                logger.warning("refused a request addressed to host %r", raw)
                await _refuse(scope, receive, send)
                return
        await self._app(scope, receive, send)


def _host_header(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key == b"host":
            return bytes(value).decode("latin-1")
    return None


async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "websocket":
        # Closing before accepting refuses the handshake; there is no body to send.
        await send({"type": "websocket.close", "code": _POLICY_VIOLATION})
        return
    response = JSONResponse(
        status_code=400, content={"code": "bad_host", "detail": "That request was blocked."}
    )
    await response(scope, receive, send)
