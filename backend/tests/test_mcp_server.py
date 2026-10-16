"""A tool server end to end: the fake one in `mcp_server.py` started as a real process,
its version agreed in either era, its tools listed, the approved ones offered, called,
crashed and stopped."""

from __future__ import annotations

import asyncio
import ctypes
import re
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from app.kernel.errors import Code
from app.kernel.registry import CapabilityRegistry
from app.services.mcp import protocol
from app.services.mcp import server as server_module
from app.services.mcp.server import Limits, Server, State
from app.services.mcp.setup import ServerSetup

FAKE = str(Path(__file__).with_name("mcp_server.py"))


class Harness:
    def __init__(self, server: Server, registry: CapabilityRegistry, approved: dict[str, str]):
        self.server = server
        self.registry = registry
        self.approved = approved

    def approve(self, name: str) -> None:
        tool = next(t for t in self.server.tools if t.name == name)
        self.approved[name] = tool.pin
        self.server.offer()


@pytest.fixture
async def started(tmp_path: Path) -> AsyncIterator[Callable[..., Any]]:
    servers: list[Server] = []

    async def start(*modes: str, room: int = 12) -> Harness:
        registry = CapabilityRegistry()
        approved: dict[str, str] = {}
        setup = ServerSetup(
            id="fake", name="Fake notes", command=sys.executable, args=[FAKE, *modes]
        )

        async def changed() -> None:
            return None

        server = Server(
            setup,
            data_dir=tmp_path,
            registry=registry,
            approved=lambda: approved,
            changed=changed,
            offer=lambda _tool: len(registry) < room,
            limits=Limits(call_timeout_s=10.0),
        )
        servers.append(server)
        await server.start()
        return Harness(server, registry, approved)

    yield start
    for server in servers:
        await server.stop(quietly=True)


async def test_a_current_server_is_agreed_without_a_handshake(started: Any) -> None:
    h = await started("current")
    assert h.server.state is State.running, h.server.detail
    assert h.server.era == protocol.Era("2026-07-28", current=True, name="fake-current")
    names = [t.name for t in h.server.tools]
    assert names == ["echo", "fail", "ask", "nested"]


async def test_an_older_server_is_asked_the_older_way(started: Any) -> None:
    h = await started("older")
    assert h.server.state is State.running, h.server.detail
    assert h.server.era is not None and h.server.era.version == "2025-11-25"
    assert not h.server.era.current


async def test_a_2025_03_26_server_may_answer_in_batches(started: Any) -> None:
    h = await started("batches")
    assert h.server.state is State.running, h.server.detail
    assert len(h.server.tools) == 4


async def test_a_server_that_speaks_another_version_is_said_so(started: Any) -> None:
    h = await started("unsupported")
    assert h.server.state is State.failed
    assert "2099-01-01" in h.server.detail


async def test_nothing_is_offered_until_approved(started: Any) -> None:
    h = await started("current")
    assert len(h.registry) == 0
    h.approve("echo")
    assert h.registry.get("mcp.fake.echo") is not None
    unsupported = next(t for t in h.server.tools if t.name == "nested")
    assert unsupported.args is None and "isn't a string" in unsupported.unsupported


async def test_an_approved_tool_runs_and_reads_as_text(started: Any) -> None:
    h = await started("current")
    h.approve("echo")
    capability = h.registry.get("mcp.fake.echo")
    assert capability is not None and capability.always_ask and capability.reach == "external"
    args = capability.args_model.model_validate({"text": "hello"})
    result = await capability.handler(args)
    assert result.ok
    assert result.data["content"] == "You said: hello\n(an image it sent, not shown)"


async def test_a_tool_that_fails_or_asks_for_input_says_so(started: Any) -> None:
    h = await started("current")
    for name in ("fail", "ask"):
        h.approve(name)
    fail = h.registry.get("mcp.fake.fail")
    ask = h.registry.get("mcp.fake.ask")
    assert fail is not None and ask is not None
    failed = await fail.handler(fail.args_model.model_validate({}))
    assert (failed.ok, failed.code) == (False, Code.tool_error)
    assert "Broken" not in failed.detail  # the server's words aren't passed on unfenced
    asked = await ask.handler(ask.args_model.model_validate({}))
    assert (asked.ok, asked.code) == (False, Code.tool_needs_input)


async def test_a_tool_changed_since_its_approval_is_withdrawn(started: Any) -> None:
    h = await started("current")
    h.approve("echo")
    h.server.tools = (await started("current", "changed")).server.tools
    h.server.offer()
    assert h.registry.get("mcp.fake.echo") is None


async def test_the_cap_holds_tools_back(started: Any) -> None:
    h = await started("current", room=1)
    h.approve("echo")
    h.approve("fail")
    assert [c.name for c in h.registry] == ["mcp.fake.echo"]


async def test_a_crash_mid_call_is_said_and_it_is_started_again(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "_BACKOFF_S", (0.05, 0.05, 0.05))
    h = await started("crash")
    h.approve("echo")
    echo = h.registry.get("mcp.fake.echo")
    assert echo is not None
    result = await echo.handler(echo.args_model.model_validate({"text": "x"}))
    assert (result.ok, result.code) == (False, Code.server_unavailable)
    for _ in range(200):
        if h.server.state is State.running:
            break
        await asyncio.sleep(0.05)
    assert h.server.state is State.running  # started again, its approval kept
    assert h.registry.get("mcp.fake.echo") is not None


async def test_a_line_past_the_limit_stops_the_server(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "RESTARTS", 0)
    h = await started("long")
    h.approve("echo")
    echo = h.registry.get("mcp.fake.echo")
    assert echo is not None
    result = await echo.handler(echo.args_model.model_validate({"text": "x"}))
    assert result.code == Code.server_unavailable
    for _ in range(100):
        if h.server.state is State.failed:
            break
        await asyncio.sleep(0.05)
    assert "more at once" in h.server.detail


async def test_a_server_that_never_answers_the_probe_is_asked_the_older_way(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protocol, "PROBE_S", 0.5)
    h = await started("silent")
    assert h.server.state is State.running, h.server.detail
    assert h.server.era is not None and not h.server.era.current


@pytest.mark.skipif(sys.platform != "win32", reason="the job object is Windows'")
async def test_stopping_it_ends_what_it_started_too(started: Any) -> None:
    h = await started("current", "child")
    for _ in range(100):
        found = re.search(r"child (\d+)", h.server.tail())
        if found:
            break
        await asyncio.sleep(0.05)
    assert found is not None
    child = int(found.group(1))
    await h.server.stop()
    assert not _running(child)


def _running(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32")
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    kernel.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


async def test_a_server_that_keeps_stopping_is_left_off(
    started: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "_BACKOFF_S", (0.05, 0.05, 0.05))
    monkeypatch.setattr(server_module, "RESTARTS", 2)
    h = await started("crash-soon")
    for _ in range(300):
        if "kept stopping" in h.server.detail:
            break
        await asyncio.sleep(0.05)
    assert h.server.state is State.failed
    assert "kept stopping" in h.server.detail
    await h.server.restart()  # the user's restart forgives what went before
    assert h.server.state is State.running


async def test_one_that_dies_when_probed_is_asked_the_older_way_next_time(started: Any) -> None:
    h = await started("probe-kills")
    assert h.server.state is State.failed
    assert "stopped while I was starting it" in h.server.detail
    await asyncio.sleep(0.5)  # not taken for a crash, and not started again
    assert h.server.state is State.failed and "again" not in h.server.detail
    await h.server.start()
    assert h.server.state is State.running, h.server.detail
    assert h.server.era is not None and not h.server.era.current
