"""What the 0.37.0 review found a tool server could do, and what stops it now: a server
that won't read stalling her, a stop racing a start, a start that fails leaving its
process, an odd tool list, a tool named on a call, and an approval of words never read."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import Capability, CapabilityRegistry
from app.services.mcp import protocol
from app.services.mcp.process import Backlog, ServerProcess
from app.services.mcp.schema import arguments_model
from app.services.mcp.server import Limits, Server, State
from app.services.mcp.setup import Launch, ServerSetup, environment
from app.services.mcp.tools import read_tools
from app.services.tools.broker import ToolBroker

FAKE = str(Path(__file__).with_name("mcp_server.py"))
DEAF = "import time; time.sleep(60)"  # never reads its input


def _launch(tmp_path: Path, *args: str) -> Launch:
    return Launch(Path(sys.executable), args, environment({}), tmp_path / "work")


def test_a_server_that_wont_read_is_refused_more_and_still_stops_quickly(tmp_path: Path) -> None:
    process = ServerProcess(
        _launch(tmp_path, "-c", DEAF),
        on_line=lambda _l: None,
        on_exit=lambda _w: None,
        max_line=1024,
    )
    process.start()
    line = b"x" * 1_000_000 + b"\n"
    with pytest.raises(Backlog):
        for _ in range(200):  # the pipe fills, then the queue: never a blocked caller
            process.send(line)
    started = time.monotonic()
    process.stop()
    assert time.monotonic() - started < 6.0


def test_a_stop_before_the_process_exists_ends_it_as_it_starts(tmp_path: Path) -> None:
    process = ServerProcess(
        _launch(tmp_path, "-c", DEAF),
        on_line=lambda _l: None,
        on_exit=lambda _w: None,
        max_line=1024,
    )
    process.stop()
    with pytest.raises(OSError):
        process.start()
    assert process._process is not None and process._process.wait(5) is not None


def _server(
    tmp_path: Path, registry: CapabilityRegistry, approved: dict[str, str], *modes: str
) -> Server:
    async def changed() -> None:
        return None

    setup = ServerSetup(id="fake", name="Fake", command=sys.executable, args=[FAKE, *modes])
    return Server(
        setup,
        data_dir=tmp_path,
        registry=registry,
        approved=lambda: approved,
        changed=changed,
        offer=lambda _tool: True,
        limits=Limits(call_timeout_s=10.0),
    )


async def test_two_starts_at_once_leave_one_server_running(tmp_path: Path) -> None:
    server = _server(tmp_path, CapabilityRegistry(), {}, "current")
    await asyncio.gather(server.start(), server.start())
    assert server.state is State.running, server.detail
    await server.stop()


async def test_a_start_that_fails_ends_its_process(tmp_path: Path) -> None:
    server = _server(tmp_path, CapabilityRegistry(), {}, "unsupported")
    await server.start()
    assert server.state is State.failed
    assert server._process is None  # ended, not left running behind a failed state


async def test_a_tool_withdrawn_after_its_prompt_isnt_run(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    approved: dict[str, str] = {}
    server = _server(tmp_path, registry, approved, "current")
    await server.start()
    echo = next(t for t in server.tools if t.name == "echo")
    approved["echo"] = echo.pin
    server.offer()
    capability = registry.get("mcp.fake.echo")
    assert capability is not None
    approved.clear()  # the server changed it, or the user withdrew it, while it asked
    server.offer()
    result = await capability.handler(capability.args_model.model_validate({"text": "x"}))
    assert (result.ok, result.code) == (False, Code.server_unavailable)
    await server.stop()


def test_one_odd_tool_doesnt_stop_the_rest() -> None:
    listed = [
        {"name": "odd", "annotations": ["not", "an", "object"], "inputSchema": {"type": "object"}},
        {"name": "fine", "title": ["nor"], "inputSchema": {"type": "object"}},
    ]
    assert [(t.name, t.title) for t in read_tools("s", listed)] == [
        ("odd", "odd"),
        ("fine", "fine"),
    ]
    era = protocol._current({"supportedVersions": ["2026-07-28"], "_meta": ["odd"]})
    assert era.current and era.name == ""


def test_a_version_a_server_names_is_shown_as_text_and_cut() -> None:
    said = protocol._unsupported({"supported": ["2099\u202e-01-01" + "x" * 50]})
    assert "\u202e" not in said and "⟪U+202E⟫" in said and "x" * 30 not in said


def test_numbers_are_json_and_list_items_bounded() -> None:
    model = arguments_model(
        "t",
        {
            "type": "object",
            "properties": {
                "n": {"type": "number"},
                "many": {"type": "array", "items": {"type": "number"}},
                "words": {"type": "array", "items": {"type": "string"}},
            },
        },
    )
    for bad in ({"n": float("nan")}, {"many": [float("inf")]}, {"words": ["x" * 20_001]}):
        with pytest.raises(ValueError):
            model.model_validate(bad)


class _NeverAsked:
    async def execute(self, _req: Any) -> CapabilityResult:
        raise AssertionError("refused before the kernel")


async def test_a_call_cant_run_a_tool_it_isnt_offered() -> None:
    registry = CapabilityRegistry()

    async def done(_args: Any) -> CapabilityResult:
        return CapabilityResult(ok=True)

    args = arguments_model("t", {"type": "object", "properties": {}})
    registry.register(Capability("mcp.notes.add", "d", args, RiskTier.guarded, done))
    broker = ToolBroker(_NeverAsked(), registry, actor="veronica:voice", voice_only=True)  # type: ignore[arg-type]
    outcome = await broker.execute("mcp_notes_add", {})
    assert (outcome.ok, outcome.code) == (False, "unknown_tool")


async def test_a_stop_during_its_start_never_shows_it_failed(tmp_path: Path) -> None:
    said: list[State] = []
    server = _server(tmp_path, CapabilityRegistry(), {}, "silent")  # never answers the probe

    async def changed() -> None:
        said.append(server.state)

    server._changed = changed
    starting = asyncio.create_task(server.start())
    for _ in range(200):
        if server._process is not None and server._process.pid is not None:
            break
        await asyncio.sleep(0.01)
    await server.stop()
    await starting
    assert (server.state, server.detail) == (State.off, "")
    assert State.failed not in said  # no screen was told it failed: it was stopped
    assert server._process is None


async def test_a_stop_during_a_restart_wins_even_when_the_screen_is_told_slowly(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path, CapabilityRegistry(), {}, "current")

    async def slow() -> None:  # a bus with subscribers yields here
        await asyncio.sleep(0.02)

    server._changed = slow
    await server.start()
    restarting = asyncio.create_task(server.restart())
    await asyncio.sleep(0.01)
    await server.stop()
    await restarting
    assert server.state is State.off
    assert server._process is None  # nothing left running that nothing tracks


async def test_a_server_switched_off_during_its_restart_is_off_and_offers_nothing(
    tmp_path: Path,
) -> None:
    from app.core.event_bus import InProcessEventBus
    from app.services.mcp.runtime import McpRuntime
    from app.services.mcp.store import McpStore

    registry = CapabilityRegistry()
    bus = InProcessEventBus()
    store = McpStore(bus, tmp_path / "mcp.json", b"k" * 32)
    runtime = McpRuntime(
        store, registry, bus, tmp_path, limits=Limits(call_timeout_s=10.0), per_turn=12
    )
    setup = ServerSetup(id="fake", name="Fake", command=sys.executable, args=[FAKE])
    await runtime.put(setup)
    for _ in range(200):
        if runtime.server("fake").running:
            break
        await asyncio.sleep(0.02)
    old = runtime.server("fake")
    echo = next(t for t in old.tools if t.name == "echo")
    await runtime.approve("fake", "echo", True, echo.pin)
    assert registry.get("mcp.fake.echo") is not None
    await runtime.restart("fake")
    await asyncio.sleep(0.01)
    await runtime.put(setup.model_copy(update={"enabled": False}))
    await asyncio.sleep(1.0)  # let the old restart run its course
    assert not old.running and old._process is None
    assert runtime.server("fake").state is State.off
    assert registry.get("mcp.fake.echo") is None
    await runtime.aclose()


class _Held:
    """What a start opened, standing in for its process: ended when the server closes."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


async def test_a_stop_while_its_process_opens_ends_what_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path, CapabilityRegistry(), {}, "current")
    opening, go_on = asyncio.Event(), asyncio.Event()
    held = _Held()

    async def held_open() -> None:
        opening.set()
        await go_on.wait()
        server._process = held  # type: ignore[assignment]

    monkeypatch.setattr(server, "_open", held_open)
    starting = asyncio.create_task(server.start())
    await opening.wait()
    await server.stop()
    go_on.set()
    await starting
    assert server.state is State.off
    assert held.stopped and server._process is None


async def test_a_stop_while_the_screen_hears_of_the_start_opens_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path, CapabilityRegistry(), {}, "current")
    telling, go_on = asyncio.Event(), asyncio.Event()
    opened: list[bool] = []

    async def changed() -> None:
        if server.state is State.starting:
            telling.set()
            await go_on.wait()

    async def never_open() -> None:
        opened.append(True)

    server._changed = changed
    monkeypatch.setattr(server, "_open", never_open)
    starting = asyncio.create_task(server.start())
    await telling.wait()
    await server.stop(quietly=True)
    go_on.set()
    await starting
    assert opened == [] and server.state is State.off


async def test_a_retired_server_neither_starts_nor_offers(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    approved: dict[str, str] = {}
    server = _server(tmp_path, registry, approved, "current")
    await server.start()
    echo = next(t for t in server.tools if t.name == "echo")
    approved["echo"] = echo.pin
    server.offer()
    assert registry.get("mcp.fake.echo") is not None
    server.retired = True
    server.offer()
    assert registry.get("mcp.fake.echo") is None  # the new one's to offer, not this one's
    await server.stop()
    await server.start()
    assert server.state is State.off and server._process is None


async def test_a_replaced_or_removed_server_cant_be_started_again(tmp_path: Path) -> None:
    from app.core.event_bus import InProcessEventBus
    from app.services.mcp.runtime import McpRuntime
    from app.services.mcp.store import McpStore

    bus = InProcessEventBus()
    store = McpStore(bus, tmp_path / "mcp.json", b"k" * 32)
    runtime = McpRuntime(
        store, CapabilityRegistry(), bus, tmp_path, limits=Limits(call_timeout_s=10.0), per_turn=12
    )
    setup = ServerSetup(id="fake", name="Fake", command=sys.executable, args=[FAKE])
    await runtime.put(setup.model_copy(update={"enabled": False}))
    replaced = runtime.server("fake")
    await runtime.put(setup.model_copy(update={"enabled": False, "name": "Fake two"}))
    removed = runtime.server("fake")
    await runtime.remove("fake")
    for old in (replaced, removed):
        await old.start()  # a crash's restart, say, still due on it
        assert old.state is State.off and old._process is None
    await runtime.aclose()
