"""The Model Context Protocol as a client speaks it: agree a version, list the tools,
call one. Both eras, since most servers people run still speak the older one.

**Current (2026-07-28), stateless.** No handshake: every request carries her version,
capabilities and name in `params._meta`. A server is asked `server/discover` first:
a result naming our version means it speaks it; the error for a version it doesn't
(-32022) means it is current but can't speak ours, and a client must not fall back.
Anything else, or no answer in `PROBE_S`, is an older server.

**Older (2025-11-25 and before).** `initialize`, then `notifications/initialized`. The
server answers with the version it will speak; one we don't know is refused.

She declares no client capabilities in either era, so a server can't ask her for
sampling, roots or the user's input. A tool call the server answers with
`input_required` is retried with the state it sent when that is all it asks for, a
few times, and otherwise refused in words (`NeedsInput`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.text_safety import reveal
from app.core.version import APP_NAME, APP_VERSION
from app.services.mcp.rpc import Channel, Gone, RpcError

CURRENT = "2026-07-28"
OLDER = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_BATCHES = "2025-03-26"  # the one revision that allowed JSON-RPC batches
_UNSUPPORTED_VERSION = -32022
_PREFIX = "io.modelcontextprotocol/"
PROBE_S = 10.0
# An older server is often started through npx, which may fetch the package first.
INITIALIZE_S = 60.0
LIST_S = 30.0
_MAX_PAGES = 20
MAX_TOOLS = 200
_RETRIES = 3
_CLIENT = {"name": APP_NAME, "version": APP_VERSION}


class Disagreed(Exception):
    """No version both sides speak; the message is for the user."""


class NeedsInput(Exception):
    """The tool asked for something she can't give (the user's input, a model)."""


class Invalid(Exception):
    """An answer that doesn't follow the protocol."""


@dataclass(frozen=True, slots=True)
class Era:
    """What was agreed with one server."""

    version: str
    current: bool
    name: str = ""  # the name the server gives itself, for the user to read


def _meta() -> dict[str, Any]:
    return {
        f"{_PREFIX}protocolVersion": CURRENT,
        f"{_PREFIX}clientInfo": _CLIENT,
        f"{_PREFIX}clientCapabilities": {},
    }


def params(era: Era, **values: Any) -> dict[str, Any]:
    """A request's params in `era`: the current one carries `_meta` on every request."""
    return {**values, "_meta": _meta()} if era.current else values


async def agree(channel: Channel, *, older_first: bool = False) -> Era:
    """The era to speak with this server. `older_first`: it went away while it was
    probed last time, so it is asked the older way straight off."""
    if not older_first:
        try:
            found = await channel.request("server/discover", {"_meta": _meta()}, timeout=PROBE_S)
        except RpcError as error:
            if error.code == _UNSUPPORTED_VERSION:
                raise Disagreed(_unsupported(error.data)) from None
        except TimeoutError:
            pass
        else:
            return _current(found)
    return await _initialize(channel)


def _current(found: Any) -> Era:
    if not isinstance(found, dict):
        raise Invalid("its discover answer isn't an object")
    versions = found.get("supportedVersions")
    if not isinstance(versions, list) or CURRENT not in versions:
        raise Disagreed(_unsupported({"supported": versions}))
    meta = found.get("_meta")
    info = meta.get(f"{_PREFIX}serverInfo") if isinstance(meta, dict) else None
    name = info.get("name") if isinstance(info, dict) else None
    return Era(CURRENT, current=True, name=name if isinstance(name, str) else "")


async def _initialize(channel: Channel) -> Era:
    request = {"protocolVersion": OLDER[0], "capabilities": {}, "clientInfo": _CLIENT}
    found = await channel.request("initialize", request, timeout=INITIALIZE_S, cancellable=False)
    if not isinstance(found, dict):
        raise Invalid("its initialize answer isn't an object")
    version = found.get("protocolVersion")
    if version not in OLDER:
        raise Disagreed(_unsupported({"supported": [version]}))
    channel.batches = version == _BATCHES
    await channel.notify("notifications/initialized")
    info = found.get("serverInfo")
    name = info.get("name") if isinstance(info, dict) else None
    return Era(str(version), current=False, name=name if isinstance(name, str) else "")


def _unsupported(data: Any) -> str:
    versions = data.get("supported") if isinstance(data, dict) else None
    found = versions if isinstance(versions, list) else []
    # Its own words, so as Settings may show them: hidden characters shown, each cut short.
    named = [reveal(v)[:20] for v in found if isinstance(v, str)][:4]
    spoken = f" It speaks {', '.join(named)}." if named else ""
    return f"It speaks a version of the protocol I don't.{spoken}"


async def list_tools(channel: Channel, era: Era) -> tuple[list[dict[str, Any]], int | None]:
    """Every tool the server offers, page by page, up to `MAX_TOOLS`, and how long the
    list may be kept (`ttlMs`), when it says."""
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    ttl: int | None = None
    for _page in range(_MAX_PAGES):
        values = {"cursor": cursor} if cursor else {}
        found = _complete(
            await channel.request("tools/list", params(era, **values), timeout=LIST_S)
        )
        page = found.get("tools")
        if not isinstance(page, list):
            raise Invalid("its tool list isn't a list")
        tools.extend(tool for tool in page if isinstance(tool, dict))
        if isinstance(found.get("ttlMs"), int):
            ttl = max(0, found["ttlMs"])
        cursor = found.get("nextCursor") if isinstance(found.get("nextCursor"), str) else None
        if not cursor or len(tools) >= MAX_TOOLS:
            break
    return tools[:MAX_TOOLS], ttl


async def call_tool(
    channel: Channel, era: Era, name: str, arguments: dict[str, Any], *, timeout: float
) -> dict[str, Any]:
    """The server's result for one call, complete."""
    values: dict[str, Any] = {"name": name, "arguments": arguments}
    for _attempt in range(_RETRIES):
        found = await channel.request("tools/call", params(era, **values), timeout=timeout)
        if not isinstance(found, dict):
            raise Invalid("its answer isn't an object")
        kind = found.get("resultType", "complete")
        if kind == "complete":
            return found
        if kind != "input_required":
            raise Invalid("its answer is of a kind I don't know")
        if found.get("inputRequests") or "requestState" not in found:
            raise NeedsInput
        values = {**values, "requestState": found["requestState"]}
    raise NeedsInput


def _complete(found: Any) -> dict[str, Any]:
    if not isinstance(found, dict):
        raise Invalid("its answer isn't an object")
    if found.get("resultType", "complete") != "complete":
        raise Invalid("its answer asks for input a list can't need")
    return found


__all__ = [
    "CURRENT",
    "OLDER",
    "Disagreed",
    "Era",
    "Gone",
    "Invalid",
    "NeedsInput",
    "RpcError",
    "agree",
    "call_tool",
    "list_tools",
]
