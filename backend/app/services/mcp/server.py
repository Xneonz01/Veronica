"""One tool server while she runs: started, its version agreed, its tools listed and the
approved ones offered, restarted after a crash, stopped.

A server that won't start, or stops, never stops her: its state says why, in words for
Settings (`detail`), and its tools are withdrawn, so a call the model makes anyway is
refused as one she doesn't have. A server that stops while running is started again,
up to `RESTARTS` times, waiting longer each time; then it stays down until the user
starts it. One that fails while starting isn't retried: its setup is the user's to fix.

Starts are taken one at a time, and each knows its generation: a stop, or a newer
start, makes an older start give up quietly, ending its process, instead of reporting
on a server that has moved on (a double click on Restart, a change mid-start).

Its tool list is read again when the server says it may have changed (an older server's
`list_changed`, debounced), when its `ttlMs` runs out (between a minute and an hour,
five minutes when it doesn't say), and on a restart.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.kernel.capability import CapabilityResult
from app.kernel.registry import CapabilityRegistry
from app.services.mcp import outcomes, protocol
from app.services.mcp.process import ServerProcess
from app.services.mcp.rpc import Channel, Gone, decode
from app.services.mcp.setup import ServerSetup, launch
from app.services.mcp.tasks import Tasks
from app.services.mcp.tools import Tool, capability, capability_name, read_tools

logger = logging.getLogger(__name__)

RESTARTS = 3
_BACKOFF_S = (2.0, 10.0, 30.0)
# Up this long, a server's next crash counts as its first again: one that crashes soon
# after every start would otherwise be started again for ever.
_STABLE_S = 60.0
_TTL_S = (60.0, 3600.0, 300.0)  # least, most, and when it doesn't say
_DEBOUNCE_S = 1.0
MAX_LINE = 4 * 1024 * 1024


class State(StrEnum):
    off = "off"  # switched off in Settings
    starting = "starting"
    running = "running"
    failed = "failed"  # won't start, or stopped too often; `detail` says why


@dataclass(frozen=True, slots=True)
class Limits:
    call_timeout_s: float
    in_flight: int = 8


class Server:
    def __init__(
        self,
        setup: ServerSetup,
        *,
        data_dir: Path,
        registry: CapabilityRegistry,
        approved: Callable[[], dict[str, str]],
        changed: Callable[[], Awaitable[None]],
        offer: Callable[[Tool], bool],
        limits: Limits,
    ) -> None:
        self.setup = setup
        self._data_dir = data_dir
        self._registry = registry
        self.approved = approved
        self._changed = changed
        self._offer = offer  # whether the per-turn cap leaves room for one more
        self._limits = limits
        self.state = State.off
        self.detail = ""
        self.era: protocol.Era | None = None
        self.tools: list[Tool] = []
        self.offered_names: set[str] = set()
        self._process: ServerProcess | None = None
        self._channel: Channel | None = None
        self._restarts = 0
        self._up_since = 0.0
        self._older_first = False
        self._generation = 0
        # Set when the runtime replaces or removes it: it never starts or offers again.
        self.retired = False
        self._one_start = asyncio.Lock()
        self._tasks = Tasks()
        self._refreshing: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self.state is State.running

    def tail(self) -> str:
        return self._process.tail() if self._process is not None else ""

    async def start(self) -> None:
        """Start it and offer its approved tools; on failure, say why and stay down."""
        async with self._one_start:
            if self.retired:
                return
            self._generation += 1
            generation = self._generation
            await self._close()
            # Every await below may let a stop in: one that does wins, and what this
            # start made is ended rather than left running untracked.
            if generation != self._generation:
                return
            self.state, self.detail, self._up_since = State.starting, "", 0.0
            await self._changed()
            if generation != self._generation:
                return
            try:
                await self._open()
            except Exception as exc:  # whatever it was, she starts; the screen says why
                if generation == self._generation:  # not stopped since
                    self._older_first = self._older_first or isinstance(exc, Gone)
                    await self._close()
                    await self._failed(outcomes.why_failed(exc))
                else:
                    await self._close()
                return
            if generation != self._generation:
                await self._close()
                return
            self.state, self._up_since = State.running, asyncio.get_running_loop().time()
            await self._changed()

    async def restart(self) -> None:
        """Start it again because the user asked: its crashes so far are forgiven."""
        self._restarts = 0
        await self.start()

    async def stop(self, *, quietly: bool = False) -> None:
        """Withdraw its tools and end its process, and every process it started. A start
        in progress gives up."""
        self._generation += 1
        await self._close()
        self.state, self.detail = State.off, ""
        if not quietly:  # quietly: replaced or restarted, its screen about to hear more
            await self._changed()

    async def refresh(self) -> None:
        """Read its tool list again, and offer what is approved of it."""
        if self.running:
            try:
                await self._list()
            except Exception as exc:
                logger.info("a tool server's list couldn't be read again: %s", type(exc).__name__)
            await self._changed()

    def offer(self) -> None:
        """Offer exactly its approved tools, as they are now: after an approval changes,
        or a new list. A tool approved as it was, and since changed, is withdrawn."""
        if self.retired:  # replaced or removed: its tools are the new one's to offer
            self._withdraw_all()
            return
        approved = self.approved()
        wanted = {
            tool.capability: tool
            for tool in self.tools
            if tool.args is not None and approved.get(tool.name) == tool.pin
        }
        for name in self.offered_names - set(wanted):
            self._registry.unregister(name)
        self.offered_names &= set(wanted)
        for name, tool in wanted.items():
            if name not in self.offered_names and self._offer(tool):
                self._registry.register(
                    capability(
                        tool,
                        self.setup.name,
                        self._call,
                        running=lambda: self.running,
                        timeout_s=self._limits.call_timeout_s + 5.0,
                        internet=self.setup.internet,
                    )
                )
                self.offered_names.add(name)

    def offered(self) -> list[Tool]:
        """The tools it offers now."""
        return [tool for tool in self.tools if tool.capability in self.offered_names]

    async def _open(self) -> None:
        how = launch(self.setup, self._data_dir)
        loop = asyncio.get_running_loop()
        channel = self._channel = Channel(
            self._send,
            in_flight=self._limits.in_flight,
            on_notification=lambda method, _params: self._notified(method),
        )

        def on_line(line: bytes) -> None:  # its reader thread: decoded there, off the loop
            batch = line.lstrip().startswith(b"[")
            loop.call_soon_threadsafe(channel.take, decode(line), batch)

        def on_exit(why: str) -> None:
            loop.call_soon_threadsafe(self._went, channel, why)

        self._process = ServerProcess(how, on_line=on_line, on_exit=on_exit, max_line=MAX_LINE)
        await asyncio.to_thread(self._process.start)
        self.era = await protocol.agree(channel, older_first=self._older_first)
        channel.answers_requests = not self.era.current
        await self._list()

    async def _close(self) -> None:
        self._tasks.cancel()
        self._withdraw_all()
        process, self._process = self._process, None
        channel, self._channel = self._channel, None
        if channel is not None:
            channel.gone("stopped")
        if process is not None:
            await asyncio.to_thread(process.stop)

    async def _call(self, tool: str, arguments: dict[str, Any]) -> CapabilityResult:
        name = self.setup.name
        if not self.running:
            return outcomes.down(f"{name} isn't running.")
        # Withdrawn since the prompt was raised: changed by the server, or no longer allowed.
        if capability_name(self.setup.id, tool) not in self.offered_names:
            return outcomes.down(f"That tool of {name}'s has changed, so I didn't use it.")
        timeout = self._limits.call_timeout_s
        return await outcomes.call(self._channel, self.era, name, tool, arguments, timeout=timeout)

    async def _list(self) -> None:
        channel, era = self._channel, self.era
        if channel is None or era is None:
            return
        listed, ttl_ms = await protocol.list_tools(channel, era)
        self.tools = read_tools(self.setup.id, listed)
        self.offer()
        self._again_in(_clamped(ttl_ms))

    def _again_in(self, seconds: float) -> None:
        if self._refreshing is not None and self._refreshing is not asyncio.current_task():
            self._refreshing.cancel()

        async def later() -> None:
            await asyncio.sleep(seconds)
            await self.refresh()

        self._refreshing = self._tasks.spawn(later())

    def _notified(self, method: str) -> None:
        if method == "notifications/tools/list_changed" and self.running:
            self._again_in(_DEBOUNCE_S)

    def _went(self, channel: Channel, why: str) -> None:
        """Its process ended, or sent a line too long to keep reading (reader thread,
        brought to the loop)."""
        channel.gone(why)
        # Only a server that was running crashed: one still starting fails its start,
        # which says why, and is left for the user to fix rather than retried.
        if channel is not self._channel or self.state is not State.running:
            return
        self._tasks.spawn(self._after_crash(why))

    async def _after_crash(self, why: str) -> None:
        self._withdraw_all()
        stopped = outcomes.EXIT.get(why, outcomes.EXIT["exited"])
        if asyncio.get_running_loop().time() - self._up_since > _STABLE_S:
            self._restarts = 0
        if self._restarts >= RESTARTS:
            await self._failed(f"{stopped} It kept stopping, so I left it off.")
            return
        await self._failed(f"{stopped} Starting it again…")
        await asyncio.sleep(_BACKOFF_S[self._restarts])
        self._restarts += 1
        await self.start()

    async def _failed(self, why: str) -> None:
        self.state, self.detail = State.failed, why
        await self._changed()

    def _withdraw_all(self) -> None:
        for name in self.offered_names:
            self._registry.unregister(name)
        self.offered_names.clear()

    async def _send(self, line: bytes) -> None:
        process = self._process
        if process is None:
            raise BrokenPipeError("not running")
        process.send(line)  # queued: never waits on a server that isn't reading


def _clamped(ttl_ms: int | None) -> float:
    least, most, unsaid = _TTL_S
    return unsaid if ttl_ms is None else min(most, max(least, ttl_ms / 1000))
