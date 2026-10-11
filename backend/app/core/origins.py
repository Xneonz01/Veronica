"""One allow-list for every cross-origin decision: CORS, WebSocket handshakes, CSRF.

The backend binds to localhost, but "localhost" is not a trust boundary: any page the
user visits can reach it. CORS alone is not enough, because WebSockets bypass CORS
entirely and "simple" POSTs (bodyless or multipart) are sent cross-origin without a
preflight. So the same allow-list is applied at all three doors, from this one
definition. The Host header is checked before any of them (`api/host_guard.py`),
which is what stops DNS rebinding.

Rule: a request carrying an ``Origin`` we don't recognise is refused. A request with
no ``Origin`` is allowed: browsers always send it on WebSocket handshakes and on
cross-origin requests, and a hostile page cannot suppress it, so the absent case is a
non-browser client (the Tauri shell, tests, curl) rather than an attack.

The list is exact. It used to admit any localhost port, which let a page served from
any other port on the machine (another dev server, a documentation preview, something
hostile) drive the API.
"""

from __future__ import annotations

ALLOWED_ORIGINS: tuple[str, ...] = (
    # The Vite dev server, which `frontend/vite.config.ts` pins to this port.
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    # Both `tauri.localhost` schemes are needed. Tauri (`manager/mod.rs`) serves the
    # packaged app from `http://tauri.localhost` on Windows and Android (`https://`
    # only when `useHttpsScheme` is set), and from `tauri://localhost` elsewhere.
    # `tauri dev` serves from the Vite origin above, so a missing entry only shows up
    # in a packaged build.
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


def is_trusted_origin(origin: str | None, *, own_origin: str | None = None) -> bool:
    """True if this Origin may drive the API.

    An absent Origin is a non-browser caller. `own_origin` is this server's own address
    as the request reached it: a page it served itself (the API docs) is same-origin,
    not a stranger. Exact string comparison, not a pattern: the old regex is where
    "any localhost port" hid.
    """
    if origin is None:
        return True
    return origin in ALLOWED_ORIGINS or (own_origin is not None and origin == own_origin)
