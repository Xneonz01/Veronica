"""Tool servers through the API Settings uses, with the fake server as a real process:
added and started, a tool approved and offered, variables never returned, changed,
restarted and removed."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.http_guards import install as install_guards
from app.api.routes_mcp import router
from app.core.event_bus import InProcessEventBus
from app.kernel.registry import CapabilityRegistry
from app.services.mcp.runtime import McpRuntime
from app.services.mcp.server import Limits
from app.services.mcp.store import McpStore

FAKE = str(Path(__file__).with_name("mcp_server.py"))
TOKEN = "sk-proj-FAKE-route-0123456789"


@pytest.fixture
def api(tmp_path: Path) -> Iterator[tuple[TestClient, CapabilityRegistry, McpRuntime]]:
    bus = InProcessEventBus()
    registry = CapabilityRegistry()
    store = McpStore(bus, tmp_path / "mcp.json", os.urandom(32))
    runtime = McpRuntime(
        store, registry, bus, tmp_path, limits=Limits(call_timeout_s=10.0), per_turn=12
    )
    app = FastAPI()
    install_guards(app)
    app.include_router(router)
    app.state.mcp = runtime
    with TestClient(app) as client:
        yield client, registry, runtime
        client.portal.call(runtime.aclose)  # type: ignore[union-attr]


def _setup(**changes: Any) -> dict[str, Any]:
    return {"id": "notes", "name": "Notes", "command": sys.executable, "args": [FAKE], **changes}


def _server(client: TestClient, state: str = "running") -> dict[str, Any]:
    for _ in range(200):
        servers = client.get("/mcp").json()["servers"]
        if servers and servers[0]["state"] == state:
            return dict(servers[0])
        time.sleep(0.05)
    raise AssertionError(f"never {state}: {client.get('/mcp').json()}")


def _allow(client: TestClient, tool: str) -> Any:
    """Approve `tool` as the listing shows it, as the screen does."""
    listed = client.get("/mcp").json()["servers"][0]["tools"]
    pin = next(t["pin"] for t in listed if t["name"] == tool)
    return client.put(f"/mcp/servers/notes/tools/{tool}", json={"approved": True, "pin": pin})


def test_a_server_added_starts_and_its_tools_wait_for_approval(api: Any) -> None:
    client, registry, _ = api
    added = client.post("/mcp/servers", json=_setup(env={"NOTES_KEY": TOKEN}))
    assert added.status_code == 200, added.text
    server = _server(client)
    assert server["env"] == ["NOTES_KEY"] and TOKEN not in client.get("/mcp").text
    states = {tool["name"]: tool["state"] for tool in server["tools"]}
    assert states == {"echo": "new", "fail": "new", "ask": "new", "nested": "unsupported"}
    assert len(registry) == 0

    approved = _allow(client, "echo")
    echo = next(t for t in approved.json()["servers"][0]["tools"] if t["name"] == "echo")
    assert (echo["state"], echo["offered"]) == ("approved", True)
    assert registry.get("mcp.notes.echo") is not None
    client.put("/mcp/servers/notes/tools/echo", json={"approved": False})
    assert registry.get("mcp.notes.echo") is None


def test_an_unsupported_tool_cant_be_approved(api: Any) -> None:
    client, _, _ = api
    client.post("/mcp/servers", json=_setup())
    _server(client)
    refused = _allow(client, "nested")
    assert refused.status_code == 404 and refused.json()["code"] == "tool_not_found"


def test_a_change_that_leaves_a_value_out_keeps_the_saved_one(api: Any) -> None:
    client, _, runtime = api
    client.post("/mcp/servers", json=_setup(env={"NOTES_KEY": TOKEN}))
    changed = client.put(
        "/mcp/servers/notes", json=_setup(name="My notes", env={"NOTES_KEY": None, "MORE": "x"})
    )
    assert changed.status_code == 200, changed.text
    kept = runtime.store.get("notes")
    assert kept is not None and kept.env == {"NOTES_KEY": TOKEN, "MORE": "x"}
    assert kept.name == "My notes"


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (_setup(command="python.exe"), "setup_invalid"),
        (_setup(id="Bad Id"), "setup_invalid"),
        (_setup(env={"NODE_OPTIONS": "--require x"}), "setup_invalid"),
    ],
)
def test_a_setup_that_cant_be_used_is_refused_in_words(api: Any, setup: Any, code: str) -> None:
    client, _, _ = api
    refused = client.post("/mcp/servers", json=setup)
    assert refused.status_code == 422 and refused.json()["code"] == code
    assert refused.json()["detail"]


def test_one_id_one_server(api: Any) -> None:
    client, _, _ = api
    client.post("/mcp/servers", json=_setup())
    assert client.post("/mcp/servers", json=_setup()).json()["code"] == "server_exists"
    assert client.put("/mcp/servers/other", json=_setup(id="other")).status_code == 404


def test_off_it_isnt_started_and_removed_it_is_stopped(api: Any) -> None:
    client, registry, _ = api
    client.post("/mcp/servers", json=_setup(enabled=False))
    assert _server(client, "off")["tools"] == []
    client.put("/mcp/servers/notes", json=_setup())
    _server(client)
    _allow(client, "echo")
    assert client.delete("/mcp/servers/notes").json()["servers"] == []
    assert registry.get("mcp.notes.echo") is None
    assert client.delete("/mcp/servers/notes").status_code == 404


def test_its_error_stream_can_be_read(api: Any) -> None:
    client, _, _ = api
    client.post("/mcp/servers", json=_setup())
    _server(client)
    assert "fake server ready" in client.get("/mcp/servers/notes/log").json()["log"]
    assert client.post("/mcp/servers/notes/restart").status_code == 200


def test_tools_past_the_windows_share_wait(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    registry = CapabilityRegistry()
    store = McpStore(bus, tmp_path / "mcp.json", os.urandom(32))
    runtime = McpRuntime(
        store, registry, bus, tmp_path, limits=Limits(call_timeout_s=10.0), per_turn=12, tokens=200
    )
    app = FastAPI()
    install_guards(app)
    app.include_router(router)
    app.state.mcp = runtime
    with TestClient(app) as client:
        client.post("/mcp/servers", json=_setup())
        _server(client)
        _allow(client, "echo")
        listed = _allow(client, "fail").json()
        offered = {t["name"]: t["offered"] for t in listed["servers"][0]["tools"]}
        assert offered["echo"] and not offered["fail"]  # echo's ~135 tokens fit; both don't
        assert [c.name for c in registry] == ["mcp.notes.echo"]
        client.put("/mcp/servers/notes/tools/echo", json={"approved": False})
        assert [c.name for c in registry] == ["mcp.notes.fail"]  # its room goes to the next
        client.portal.call(runtime.aclose)  # type: ignore[union-attr]


def test_an_approval_is_of_the_tool_as_it_was_shown(api: Any) -> None:
    client, registry, _ = api
    client.post("/mcp/servers", json=_setup())
    server = _server(client)
    shown = next(t for t in server["tools"] if t["name"] == "echo")
    stale = client.put("/mcp/servers/notes/tools/echo", json={"approved": True, "pin": "0" * 64})
    assert stale.status_code == 409 and stale.json()["code"] == "tool_changed"
    assert registry.get("mcp.notes.echo") is None
    fresh = client.put(
        "/mcp/servers/notes/tools/echo", json={"approved": True, "pin": shown["pin"]}
    )
    assert fresh.status_code == 200 and registry.get("mcp.notes.echo") is not None
    assert client.get("/mcp").json()["tokens"] == 1_500


def test_an_approval_names_the_tool_it_read(api: Any) -> None:
    client, _, _ = api
    client.post("/mcp/servers", json=_setup())
    _server(client)
    refused = client.put("/mcp/servers/notes/tools/echo", json={"approved": True})
    assert refused.status_code == 422 and refused.json()["code"] == "pin_missing"
    assert client.put("/mcp/servers/notes/tools/echo", json={"approved": False}).status_code == 200
