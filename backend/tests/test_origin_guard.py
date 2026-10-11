"""Cross-origin trust boundary: WebSocket handshakes and CSRF on writes.

The backend binds to localhost, but any page the user visits can reach it. CORS does
not cover WebSockets at all, and a bodyless or multipart POST is a "simple request"
sent without a preflight, so both need the same origin allowlist.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from app.api.host_guard import HostGuard, host_of
from app.core.origins import is_trusted_origin
from app.main import create_app

_EVIL = "https://evil.example"
_WS_LANES = ("/ws/events", "/ws/chat", "/ws/voice", "/ws/voice/harness")


def test_trusted_origin_allowlist() -> None:
    assert is_trusted_origin("http://localhost:5173")
    assert is_trusted_origin("tauri://localhost")
    assert is_trusted_origin(None)  # non-browser caller (Tauri native, tests, curl)
    assert not is_trusted_origin(_EVIL)
    assert not is_trusted_origin("http://localhost.evil.example")  # suffix must not match


@pytest.mark.parametrize("lane", _WS_LANES)
def test_ws_handshake_refused_from_foreign_origin(lane: str) -> None:
    # WebSockets bypass CORS: without the guard, any page could open /ws/chat and read
    # the memory store, or listen on /ws/events for memories as they are learned.
    with TestClient(create_app(), base_url="http://localhost") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"ws://localhost{lane}", headers={"origin": _EVIL}) as ws:
                ws.receive()


def test_cross_origin_write_is_blocked() -> None:
    # A bodyless POST needs no preflight, so the browser sends it and the side effect
    # lands even though the response is unreadable. reject/undo/delete must all refuse.
    with TestClient(create_app(), base_url="http://localhost") as client:
        blocked = client.post("/memory/1/reject", headers={"Origin": _EVIL})
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "untrusted_origin"
        assert client.delete("/memory/1", headers={"Origin": _EVIL}).status_code == 403


def test_same_origin_and_native_writes_still_pass() -> None:
    # The guard must not break the real frontend (Vite dev server / Tauri) or clients
    # that send no Origin at all. 403 is the only status we're ruling out here.
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert (
            client.post("/memory/999999/reject", headers={"Origin": "http://localhost:5173"})
        ).status_code != 403
        assert client.post("/memory/999999/reject").status_code != 403  # no Origin


def test_reads_are_untouched() -> None:
    with TestClient(create_app(), base_url="http://localhost") as client:
        assert client.get("/health", headers={"Origin": _EVIL}).status_code == 200


def test_other_localhost_ports_are_not_trusted() -> None:
    # The allow-list used to be "any localhost port", so a page served from any other
    # port on the machine could drive the API.
    assert not is_trusted_origin("http://localhost:9999")
    assert not is_trusted_origin("http://127.0.0.1:3000")
    assert is_trusted_origin("http://127.0.0.1:5173")
    assert is_trusted_origin("http://tauri.localhost")
    # A page the backend served itself (the API docs) is same-origin.
    assert is_trusted_origin("http://localhost:8000", own_origin="http://localhost:8000")


def test_a_rebound_host_is_refused_before_any_route() -> None:
    # DNS rebinding: a page on evil.example re-points that name at 127.0.0.1. Its
    # same-origin GETs carry no Origin, so only the Host header gives it away.
    with TestClient(create_app(), base_url="http://evil.example:8000") as client:
        refused = client.get("/memory")
        assert refused.status_code == 400
        assert refused.json()["code"] == "bad_host"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("ws://evil.example:8000/ws/events") as ws:
                ws.receive()


@pytest.mark.parametrize("base_url", ["http://localhost:8000", "http://127.0.0.1:8000"])
def test_loopback_names_are_served(base_url: str) -> None:
    with TestClient(create_app(), base_url=base_url) as client:
        assert client.get("/health").status_code == 200


async def test_the_ipv6_loopback_is_served() -> None:
    # Starlette's TestClient can't parse an IPv6 base URL, so drive the guard directly.
    reached: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        reached.append(scope["type"])

    async def never(*_args: Any) -> Any:
        raise AssertionError("a passing request must not be answered by the guard")

    await HostGuard(inner)({"type": "http", "headers": [(b"host", b"[::1]:8000")]}, never, never)
    assert reached == ["http"]


def test_host_names_keep_ipv6_brackets() -> None:
    assert host_of("[::1]:8000") == "[::1]"
    assert host_of("LocalHost:8000") == "localhost"
    assert host_of("evil.example") == "evil.example"


def test_the_servers_own_pages_can_still_write() -> None:
    with TestClient(create_app(), base_url="http://localhost:8000") as client:
        response = client.post("/memory/1/reject", headers={"Origin": "http://localhost:8000"})
        assert response.status_code != 403  # past the origin check (the memory is missing)


@pytest.mark.parametrize("lane", _WS_LANES)
def test_every_lane_under_test_exists(lane: str) -> None:
    # A renamed lane would make the refusal tests above pass for the wrong reason: a
    # route that doesn't exist refuses every handshake too. A real lane accepts a
    # handshake that carries no Origin (a non-browser client).
    with TestClient(create_app(), base_url="http://localhost") as client:
        with client.websocket_connect(f"ws://localhost{lane}"):
            pass
