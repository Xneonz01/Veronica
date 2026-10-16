"""What a tool server's failures come to: a call's result in her words, and the line
Settings shows for a server that won't start or stopped.

Never the exception's own text, or the server's: either could quote what the server
sent, and these lines go to the model or the screen.
"""

from __future__ import annotations

import logging
from typing import Any

from app.kernel.capability import CapabilityResult
from app.kernel.errors import Code
from app.services.mcp import protocol
from app.services.mcp.rpc import Channel, Gone, RpcError
from app.services.mcp.setup import SetupRefused
from app.services.mcp.tools import result

logger = logging.getLogger(__name__)

EXIT = {
    "exited": "It stopped.",
    "line_too_long": "It sent more at once than I read, so I stopped it.",
}


async def call(
    channel: Channel | None,
    era: protocol.Era | None,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout: float,
) -> CapabilityResult:
    """One call of `tool` on the server named `server`, as a capability's result."""
    if channel is None or era is None:
        return down(f"{server} isn't running.")
    try:
        found = await protocol.call_tool(channel, era, tool, arguments, timeout=timeout)
    except TimeoutError:
        detail = f"{server} took too long, so I stopped it."
        return CapabilityResult(ok=False, code=Code.capability_timeout, detail=detail)
    except Gone:
        return down(f"{server} stopped before it answered.")
    except protocol.NeedsInput:
        detail = f"{server} asked for something I can't give it, so that didn't go ahead."
        return CapabilityResult(ok=False, code=Code.tool_needs_input, detail=detail)
    except (RpcError, protocol.Invalid):
        detail = f"{server} turned that down."
        return CapabilityResult(ok=False, code=Code.tool_error, detail=detail)
    return result(found, server)


def down(detail: str) -> CapabilityResult:
    return CapabilityResult(ok=False, code=Code.server_unavailable, detail=detail)


def why_failed(exc: Exception) -> str:
    """Why a server didn't start, in words for Settings."""
    if isinstance(exc, SetupRefused | protocol.Disagreed):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "It didn't answer when I started it."
    if isinstance(exc, Gone):
        return "It stopped while I was starting it."
    if isinstance(exc, protocol.Invalid | RpcError):
        return "It answered in a way I can't read."
    if not isinstance(exc, OSError):
        logger.warning("a tool server failed to start: %s", type(exc).__name__)
    return "It couldn't be started."
