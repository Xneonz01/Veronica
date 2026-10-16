"""The tool servers the user set up, and the tools they approved, kept in her data folder
(`mcp.json`), saved atomically, with an `mcp.changed` event on every change.

A server's arguments and variables can hold keys, so they are sealed with her data key
(`core/sealed.py`), bound to the server's id so one server's can't stand in for
another's. Where there is no data key (not Windows), a server that has any isn't kept
(`CannotKeep`), as the web's Brave key isn't. Approvals are a tool's pin
(`tools.pin`): what it was when the user said yes.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.event_bus import EventBus
from app.core.sealed import SealBroken, seal, unseal
from app.schemas.events import Event
from app.services.mcp.setup import ServerSetup

logger = logging.getLogger(__name__)

MAX_SERVERS = 16
_PLAIN = ("id", "name", "command", "enabled", "internet")


class CannotKeep(Exception):
    """Arguments or variables, and no data key here to seal them with."""


class StoreFull(Exception):
    """As many servers as she keeps."""


class McpStore:
    def __init__(self, bus: EventBus, path: Path, data_key: bytes | None) -> None:
        self._bus = bus
        self._path = path
        self._key = data_key
        self._setups: dict[str, ServerSetup] = {}
        self._approved: dict[str, dict[str, str]] = {}

    def load(self) -> None:
        """What was saved; a missing or unreadable file leaves none, and a server whose
        sealed part won't open is left out, with a line in the log saying which."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for entry in raw.get("servers", []) if isinstance(raw.get("servers"), list) else []:
            setup = self._opened(entry)
            if setup is not None:
                self._setups[setup.id] = setup
        approved = raw.get("approved")
        if isinstance(approved, dict):
            for server, tools in approved.items():
                if server in self._setups and isinstance(tools, dict):
                    kept = {
                        t: p for t, p in tools.items() if isinstance(t, str) and isinstance(p, str)
                    }
                    self._approved[server] = kept

    def setups(self) -> list[ServerSetup]:
        return list(self._setups.values())

    def get(self, server: str) -> ServerSetup | None:
        return self._setups.get(server)

    def approved(self, server: str) -> dict[str, str]:
        """Tool name to the pin it was approved under."""
        return dict(self._approved.get(server, {}))

    async def put(self, setup: ServerSetup) -> None:
        """Add `setup`, or replace the one with its id. A changed command or arguments is
        another program: its approvals go with it."""
        if (setup.args or setup.env) and self._key is None:
            raise CannotKeep
        old = self._setups.get(setup.id)
        if old is None and len(self._setups) >= MAX_SERVERS:
            raise StoreFull
        if old is not None and (old.command, old.args) != (setup.command, setup.args):
            self._approved.pop(setup.id, None)
        self._setups[setup.id] = setup
        await self._changed()

    async def remove(self, server: str) -> bool:
        found = self._setups.pop(server, None) is not None
        self._approved.pop(server, None)
        if found:
            await self._changed()
        return found

    async def approve(self, server: str, tool: str, pinned: str | None) -> None:
        """Approve `tool` as it is now (`pinned`), or withdraw its approval (None)."""
        tools = self._approved.setdefault(server, {})
        if pinned is None:
            tools.pop(tool, None)
        else:
            tools[tool] = pinned
        await self._changed()

    def _opened(self, entry: Any) -> ServerSetup | None:
        if not isinstance(entry, dict):
            return None
        hidden: dict[str, Any] = {"args": [], "env": {}}
        sealed = entry.get("sealed")
        if isinstance(sealed, str):
            if self._key is None:
                return None
            try:
                blob = base64.b64decode(sealed, validate=True)
                hidden = json.loads(unseal(blob, self._key, _bound(str(entry.get("id")))))
            except (ValueError, SealBroken):
                logger.warning("a tool server's saved settings can't be opened; left out")
                return None
        try:
            return ServerSetup.model_validate({**{k: entry.get(k) for k in _PLAIN}, **hidden})
        except ValidationError:
            logger.warning("a tool server's saved settings aren't valid; left out")
            return None

    def _entry(self, setup: ServerSetup) -> dict[str, Any]:
        entry: dict[str, Any] = setup.model_dump(include=set(_PLAIN))
        if (setup.args or setup.env) and self._key is not None:
            hidden = json.dumps({"args": setup.args, "env": setup.env}).encode("utf-8")
            blob = seal(hidden, self._key, _bound(setup.id))
            entry["sealed"] = base64.b64encode(blob).decode("ascii")
        return entry

    async def _changed(self) -> None:
        self._save()
        await self._bus.publish(Event(type="mcp.changed", source="mcp", payload={}))

    def _save(self) -> None:
        """Atomic write. A failure keeps the live change: reverting it would undo what
        the user just did for a reason they can't see."""
        payload = {
            "servers": [self._entry(setup) for setup in self._setups.values()],
            "approved": {s: tools for s, tools in self._approved.items() if s in self._setups},
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, self._path)
        except OSError:
            logger.exception("could not save the tool servers; they apply until restart")


def _bound(server: str) -> str:
    return f"mcp-server-{server}"
