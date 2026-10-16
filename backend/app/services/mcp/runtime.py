"""Every tool server while she runs: started with her in the background, changed live
from Settings, stopped with her.

Starting never waits on a server: each starts on its own task, and one that won't
start only says so (`server.py`). A change in Settings applies at once: the server is
stopped and, if it is on, started again with the new setup. An approval offers or
withdraws its tool straight away. At most `per_turn` approved tools are offered in all,
taking at most `tokens` of the chat window together, filled server by server in the
order each lists its tools, a tool already offered keeping its place: every
tool's description and arguments are read by the model on every turn of chat, and a
long list both costs the window (`config_capabilities.mcp_tool_tokens`) and makes the
right tool harder to pick.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.core.config_capabilities import CapabilitySettings
from app.core.event_bus import EventBus
from app.kernel.registry import CapabilityRegistry
from app.schemas.events import Event
from app.services.mcp.server import Limits, Server, State
from app.services.mcp.setup import ServerSetup
from app.services.mcp.store import McpStore
from app.services.mcp.tasks import Tasks
from app.services.mcp.tools import Tool
from app.services.mcp.views import view

logger = logging.getLogger(__name__)

_STOP_ALL_S = 10.0


def build_mcp(
    settings: CapabilitySettings,
    bus: EventBus,
    registry: CapabilityRegistry,
    data_dir: Path,
    data_key: bytes | None,
) -> McpRuntime:
    """The tool servers as saved, not started yet (`McpRuntime.start`)."""
    store = McpStore(bus, data_dir / "mcp.json", data_key)
    store.load()
    limits = Limits(call_timeout_s=settings.mcp_call_timeout_s)
    return McpRuntime(
        store,
        registry,
        bus,
        data_dir,
        limits=limits,
        per_turn=settings.mcp_tools_per_turn,
        tokens=settings.mcp_tool_tokens,
    )


class UnknownServer(LookupError):
    pass


class UnknownTool(LookupError):
    pass


class ToolChanged(LookupError):
    """The tool changed between the user reading it and approving it."""


class McpRuntime:
    def __init__(
        self,
        store: McpStore,
        registry: CapabilityRegistry,
        bus: EventBus,
        data_dir: Path,
        *,
        limits: Limits,
        per_turn: int,
        tokens: int = 1_500,
    ) -> None:
        self.store = store
        self._registry = registry
        self._bus = bus
        self._data_dir = data_dir
        self._limits = limits
        self._per_turn = per_turn
        self._tokens = tokens
        self._servers: dict[str, Server] = {}
        self._starting = Tasks()

    @property
    def per_turn(self) -> int:
        return self._per_turn

    @property
    def tokens(self) -> int:
        return self._tokens

    def start(self) -> None:
        """Start every server that is on, each on its own task; returns at once."""
        for setup in self.store.setups():
            self._servers[setup.id] = self._server(setup)
            if setup.enabled:
                self._starting.spawn(self._servers[setup.id].start())

    def reaching_internet(self) -> bool:
        """Whether a server the user marked as reaching the internet is running."""
        return any(s.running and s.setup.internet for s in self._servers.values())

    def view(self) -> list[dict[str, Any]]:
        return [view(self._servers[s.id]) for s in self.store.setups() if s.id in self._servers]

    def server(self, server_id: str) -> Server:
        found = self._servers.get(server_id)
        if found is None:
            raise UnknownServer(server_id)
        return found

    async def put(self, setup: ServerSetup) -> None:
        """Save `setup` (new or changed) and apply it now. The new server takes the old
        one's place before the old is stopped, so no screen sees neither."""
        await self.store.put(setup)
        old = self._servers.get(setup.id)
        server = self._servers[setup.id] = self._server(setup)
        if old is not None:
            old.retired = True
            await old.stop(quietly=True)
        if setup.enabled:
            self._starting.spawn(server.start())
        await self._announce()

    async def remove(self, server_id: str) -> None:
        server = self.server(server_id)
        server.retired = True
        await server.stop(quietly=True)
        del self._servers[server_id]
        await self.store.remove(server_id)
        self._offer_all()

    async def restart(self, server_id: str) -> None:
        server = self.server(server_id)
        if server.setup.enabled:
            self._starting.spawn(server.restart())

    async def approve(
        self, server_id: str, tool_name: str, approved: bool, seen: str | None = None
    ) -> None:
        """Approve a tool as the user saw it (`seen`, its pin then), or withdraw the
        approval; offered or not at once. `ToolChanged` when it has changed since."""
        server = self.server(server_id)
        tool = next((t for t in server.tools if t.name == tool_name), None)
        if tool is None or (approved and tool.args is None):
            raise UnknownTool(tool_name)
        if approved and seen is not None and seen != tool.pin:
            raise ToolChanged(tool_name)
        await self.store.approve(server_id, tool_name, tool.pin if approved else None)
        self._offer_all()
        await self._announce()

    async def aclose(self) -> None:
        await asyncio.gather(*self._starting.cancel(), return_exceptions=True)
        stopping = asyncio.gather(
            *(server.stop(quietly=True) for server in self._servers.values()),
            return_exceptions=True,
        )
        try:  # each stop is bounded; this bounds them all, so shutdown always ends
            await asyncio.wait_for(stopping, _STOP_ALL_S)
        except TimeoutError:
            logger.warning("tool servers didn't all stop in time; her exit ends them")

    def _server(self, setup: ServerSetup) -> Server:
        return Server(
            setup,
            data_dir=self._data_dir,
            registry=self._registry,
            approved=lambda: self.store.approved(setup.id),
            changed=self._changed,
            offer=self._room_for,
            limits=self._limits,
        )

    def _room_for(self, tool: Tool) -> bool:
        offered = [t for server in self._servers.values() for t in server.offered()]
        taken = sum(t.cost for t in offered)
        return len(offered) < self._per_turn and taken + tool.cost <= self._tokens

    def _offer_all(self) -> None:
        """Offer what is approved, server by server: one approval withdrawn makes room
        for another server's tool held back by the cap."""
        for server in self._servers.values():
            if server.state is State.running:
                server.offer()

    async def _changed(self) -> None:
        self._offer_all()
        await self._announce()

    async def _announce(self) -> None:
        await self._bus.publish(Event(type="mcp.changed", source="mcp", payload={}))
