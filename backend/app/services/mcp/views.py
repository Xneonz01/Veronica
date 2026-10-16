"""A tool server as Settings shows it: its setup (variables by name only), its state,
what it calls itself, and each tool by state, with the pin it would be approved under.

What the server chose is cleaned and cut here: its own name, like its tools'
descriptions, is a stranger's text."""

from __future__ import annotations

from typing import Any

from app.services.mcp.schema import shown
from app.services.mcp.server import Server


def view(server: Server) -> dict[str, Any]:
    approved = server.approved()
    era = server.era
    return {
        **server.setup.view(),
        "state": server.state.value,
        "detail": server.detail,
        "version": era.version if era is not None else None,
        "server_name": shown(era.name, 60) if era is not None else "",
        "tools": [
            {
                **tool.view(approved.get(tool.name)),
                "offered": tool.capability in server.offered_names,
            }
            for tool in server.tools
        ],
    }
