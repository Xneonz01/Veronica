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
from app.services.tools.broker import source_of

# The tools, by the names the model calls them (and a chat transcript stores), whose
# result is content she then puts into words: a reply after one may be its text, so
# learning doesn't file it as the user's own (`chat/learning_sources.py`).
CONTENT_TOOLS = frozenset(
    {"web_search", "web_read", "files_read", "files_search", "files_list", "memory_recall"}
)


def returns_content(tool_name: str | None) -> bool:
    """Whether a reply after this tool may be its result in her words: one of hers that
    reads, or any tool server's, whose result is whatever the server chose to send."""
    return tool_name in CONTENT_TOOLS or source_of(tool_name) == "mcp"


def read_sources(capabilities: Iterable[str | None]) -> frozenset[Source]:
    """What a reply after these calls may carry: the web's text if any was fetched, a
    tool server's if one answered, and a tool's result if any other ran."""
    return frozenset(source_of(name) for name in capabilities)


def stored_source(tool_name: str | None) -> Source:
    """The source a stored tool row stands for, by the name the model called it."""
    return source_of(tool_name)
