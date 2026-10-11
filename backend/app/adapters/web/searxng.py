"""SearXNG, a metasearch engine the user runs themselves: no key and no account, and the
query goes only to the engines their instance asks.

`GET /search?q=…&format=json` answers `{"results": [{"title", "url", "content"}, …]}`.
The JSON format must be listed under `search: formats:` in the instance's
`settings.yml`; without it SearXNG answers 403, which is said as that (`json_off`), not
as a refusal. (SearXNG's search API and `searx/webutils.py`, checked 2026-10-11.)
"""

from __future__ import annotations

import json

import httpx

from app.adapters.web.capped import TooLarge, get_capped
from app.adapters.web.search import ANSWER_LIMIT, SearchFailed, SearchHit, failure, hits_from


class SearxngProvider:
    name = "searxng"

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")

    async def search(self, query: str, count: int) -> list[SearchHit]:
        try:
            fetched = await get_capped(
                self._client,
                f"{self._base}/search",
                limit=ANSWER_LIMIT,
                params={"q": query, "format": "json"},
                headers={"Accept": "application/json"},
            )
        except (httpx.HTTPError, httpx.InvalidURL, TooLarge):
            raise SearchFailed("unreachable") from None
        if fetched.status == 403:
            raise SearchFailed("json_off")
        if (failed := failure(fetched.status)) is not None:
            raise failed
        try:
            answer = json.loads(fetched.body)
        except ValueError:
            raise SearchFailed("bad_answer") from None
        if not isinstance(answer, dict):
            raise SearchFailed("bad_answer")
        return hits_from(answer.get("results"), "content", count)
