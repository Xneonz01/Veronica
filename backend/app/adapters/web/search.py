"""Web search behind one interface (ADR 0003): a provider is an adapter, as a model is,
so another one is a new module and nothing above it changes.

A provider answers with hits or raises `SearchFailed` with a short reason the app has
words for. The response body is never passed on: a provider's error page is the
provider's text, not ours.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Literal, Protocol

Reason = Literal["unreachable", "refused", "json_off", "bad_key", "rate_limited", "bad_answer"]

# A search answer is a page of short items: anything past this is not one.
ANSWER_LIMIT = 1_000_000
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class SearchFailed(Exception):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, count: int) -> list[SearchHit]: ...


def hits_from(items: object, snippet_key: str, count: int) -> list[SearchHit]:
    """The usable results among `items`: a title and an http(s) link each, markup taken
    out of the text, at most `count`. Anything else in the list is skipped."""
    if not isinstance(items, list):
        raise SearchFailed("bad_answer")
    found: list[SearchHit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title, url, snippet = item.get("title"), item.get("url"), item.get(snippet_key)
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        if not url.startswith(("http://", "https://")):
            continue
        text = snippet if isinstance(snippet, str) else ""
        found.append(SearchHit(_plain(title, 200), url[:500], _plain(text, 400)))
        if len(found) >= count:
            break
    return found


def failure(status: int) -> SearchFailed | None:
    """What an HTTP status means for a search, None for a success."""
    if status < 400:
        return None
    if status in (401, 403):
        return SearchFailed("bad_key")
    if status == 429:
        return SearchFailed("rate_limited")
    return SearchFailed("refused")


def _plain(text: str, limit: int) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub("", text))).strip()[:limit]
