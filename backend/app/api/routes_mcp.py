"""Tool servers in Settings (ADR 0003): list them with their tools, add, change, remove,
restart, approve a tool, and read what a server wrote to its error stream.

A server's variables go in and never come out: what comes back names them, without
their values (`ServerSetup.view`), and a change that leaves a variable's value out
(`null`) keeps the one saved. Behind the same Origin guard as every route; any program
running as the user can call these too, as it can the rest of her API (SECURITY.md).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationError

from app.api.refusals import Refusal
from app.core.text_safety import reveal
from app.services.mcp.runtime import McpRuntime, ToolChanged, UnknownServer, UnknownTool
from app.services.mcp.setup import MAX_VARIABLES, ServerSetup, SetupRefused, program
from app.services.mcp.store import CannotKeep, StoreFull

router = APIRouter(prefix="/mcp", tags=["mcp"])


class SetupChange(BaseModel):
    """A setup as Settings sends it: a variable whose value is null keeps its saved one."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str | None] = Field(default_factory=dict, max_length=MAX_VARIABLES)
    enabled: bool = True
    internet: bool = False


class Approval(BaseModel):
    model_config = {"extra": "forbid"}
    approved: bool
    # The pin of the tool as the screen showed it (`GET /mcp`): approving it anew if it
    # changed since would approve words the user never read.
    pin: str | None = Field(default=None, max_length=64)


def _mcp(request: Request) -> McpRuntime:
    runtime = getattr(request.app.state, "mcp", None)
    if runtime is None:  # started without her memory (`app/stores.py`)
        raise Refusal(503, "mcp_unavailable", "Tool servers aren't available right now.")
    mcp: McpRuntime = runtime
    return mcp


def _listing(mcp: McpRuntime) -> dict[str, Any]:
    return {"servers": mcp.view(), "per_turn": mcp.per_turn, "tokens": mcp.tokens}


@router.get("")
async def list_servers(request: Request) -> dict[str, Any]:
    return _listing(_mcp(request))


@router.post("/servers")
async def add_server(change: SetupChange, request: Request) -> dict[str, Any]:
    mcp = _mcp(request)
    if mcp.store.get(change.id) is not None:
        raise Refusal(409, "server_exists", "There's already a server with that id.")
    await _put(mcp, change, saved=None)
    return _listing(mcp)


@router.put("/servers/{server_id}")
async def change_server(server_id: str, change: SetupChange, request: Request) -> dict[str, Any]:
    mcp = _mcp(request)
    saved = mcp.store.get(server_id)
    if saved is None or change.id != server_id:
        raise Refusal(404, "server_not_found", "That server isn't set up.")
    await _put(mcp, change, saved=saved)
    return _listing(mcp)


@router.delete("/servers/{server_id}")
async def remove_server(server_id: str, request: Request) -> dict[str, Any]:
    mcp = _mcp(request)
    try:
        await mcp.remove(server_id)
    except UnknownServer:
        raise Refusal(404, "server_not_found", "That server isn't set up.") from None
    return _listing(mcp)


@router.post("/servers/{server_id}/restart")
async def restart_server(server_id: str, request: Request) -> dict[str, Any]:
    mcp = _mcp(request)
    try:
        await mcp.restart(server_id)
    except UnknownServer:
        raise Refusal(404, "server_not_found", "That server isn't set up.") from None
    return _listing(mcp)


@router.put("/servers/{server_id}/tools/{tool}")
async def approve_tool(
    server_id: str, tool: str, approval: Approval, request: Request
) -> dict[str, Any]:
    mcp = _mcp(request)
    if approval.approved and approval.pin is None:
        raise Refusal(422, "pin_missing", "Say which version of the tool you looked at.")
    try:
        await mcp.approve(server_id, tool, approval.approved, approval.pin)
    except UnknownServer:
        raise Refusal(404, "server_not_found", "That server isn't set up.") from None
    except UnknownTool:
        raise Refusal(404, "tool_not_found", "That server doesn't offer that tool.") from None
    except ToolChanged:
        detail = "That tool changed since you looked at it. Read it again before allowing it."
        raise Refusal(409, "tool_changed", detail) from None
    return _listing(mcp)


@router.get("/servers/{server_id}/log")
async def server_log(server_id: str, request: Request) -> dict[str, str]:
    """The end of what the server wrote to its error stream, hidden characters shown."""
    try:
        server = _mcp(request).server(server_id)
    except UnknownServer:
        raise Refusal(404, "server_not_found", "That server isn't set up.") from None
    return {"log": reveal(server.tail())}


async def _put(mcp: McpRuntime, change: SetupChange, *, saved: ServerSetup | None) -> None:
    kept = saved.env if saved is not None else {}
    env = {
        name: kept.get(name, "") if value is None else value for name, value in change.env.items()
    }
    try:
        setup = ServerSetup.model_validate({**change.model_dump(), "env": env})
        program(setup.command)
    except ValidationError as invalid:
        raise Refusal(422, "setup_invalid", _first(invalid)) from None
    except SetupRefused as refused:
        raise Refusal(422, "setup_invalid", str(refused)) from None
    try:
        await mcp.put(setup)
    except CannotKeep:
        detail = "Its arguments can't be kept safely on this system, so I didn't save it."
        raise Refusal(409, "cannot_keep", detail) from None
    except StoreFull:
        raise Refusal(409, "too_many_servers", "That's as many servers as I keep.") from None


def _first(invalid: ValidationError) -> str:
    """The first problem in words: the validators' own messages, which never quote a
    value."""
    error = invalid.errors()[0]
    where = ".".join(str(part) for part in error.get("loc", ())) or "the setup"
    message = str(error.get("msg", "isn't valid")).removeprefix("Value error, ")
    return f"{where}: {message}"[:200]
