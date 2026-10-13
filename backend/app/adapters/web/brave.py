"""Brave Search's API, for whoever would rather use a key than run a search engine.

`GET https://api.search.brave.com/res/v1/web/search?q=…&count=…` with the key in
`X-Subscription-Token`; at most 20 results a page; the hits are `web.results`, each
with `title`, `url` and `description` (Brave's API documentation, checked 2026-10-11).
"""

from __future__ import annotations

import json

import httpx

from app.adapters.web.capped import BadEncoding, TooLarge, get_capped
from app.adapters.web.search import ANSWER_LIMIT, SearchFailed, SearchHit, failure, hits_from

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_MAX_COUNT = 20


class BraveProvider:
    name = "brave"

    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self._client = client
        self._key = key

    async def search(self, query: str, count: int) -> list[SearchHit]:
        try:
            fetched = await get_capped(
                self._client,
                ENDPOINT,
                limit=ANSWER_LIMIT,
                params={"q": query, "count": min(count, _MAX_COUNT)},
                headers={"Accept": "application/json", "X-Subscription-Token": self._key},
            )
        except UnicodeEncodeError:
            # A key that isn't ASCII can't go in a header, and the error quotes it whole.
            raise SearchFailed("bad_key") from None
        except (httpx.HTTPError, TooLarge, BadEncoding):
            raise SearchFailed("unreachable") from None
        if (failed := failure(fetched.status)) is not None:
            raise failed
        try:
            answer = json.loads(fetched.body)
        except ValueError:
            raise SearchFailed("bad_answer") from None
        web = answer.get("web") if isinstance(answer, dict) else None
        if not isinstance(web, dict):
            return []  # an answer with no web results at all, which Brave can give
        return hits_from(web.get("results"), "description", count)
