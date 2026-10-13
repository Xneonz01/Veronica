"""Where a reply's words may have come from, so later turns treat it as they would that.

A reply after a tool call may repeat what the tool returned, and a sentence she repeated
one turn is no less a page's the next (`kernel/taint.py`). A reply keeps every source it
may carry, not one: one that recited a file and a page is the user's own (it must not
leave unasked, `kernel/egress.py`) and the web's (it makes actions ask,
`kernel/verdict.py`) at once. One label, the web's, dropped the file, and a single
"tool result" label dropped the web, so the rule that a turn after reading the web asks
first lapsed one turn later.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.kernel.taint import Source
from app.services.tools.broker import from_web

# The tools, by the names the model calls them (and a chat transcript stores), whose
# result is content she then puts into words: a reply after one may be its text, so
# learning doesn't file it as the user's own (`chat/learning_sources.py`).
CONTENT_TOOLS = frozenset(
    {"web_search", "web_read", "files_read", "files_search", "files_list", "memory_recall"}
)


def read_sources(capabilities: Iterable[str | None]) -> frozenset[Source]:
    """What a reply after these calls may carry: the web's text if any was fetched,
    and a tool's result if any other ran."""
    names = list(capabilities)
    found: set[Source] = set()
    if any(from_web(name) for name in names):
        found.add("web")
    if any(not from_web(name) for name in names):
        found.add("tool_result")
    return frozenset(found)


def stored_source(tool_name: str | None) -> Source:
    """The source a stored tool row stands for, by the name the model called it."""
    return "web" if (tool_name or "").startswith("web_") else "tool_result"
