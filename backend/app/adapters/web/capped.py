"""One GET, read no further than a limit.

A page or a search answer is fed from outside, so its size is the sender's choice:
streamed and stopped at the cap, a 2 GB "page" costs what the cap allows and no more.
Redirects are never followed here; the caller decides what to do with one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import httpx


class TooLarge(Exception):
    """The body went past the limit."""


@dataclass(frozen=True, slots=True)
class Fetched:
    status: int
    headers: httpx.Headers
    body: bytes


async def get_capped(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    *,
    limit: int,
    params: Mapping[str, str | int] | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: dict[str, object] | None = None,
) -> Fetched:
    """The response to a GET of `url`, its body cut off with `TooLarge` past `limit`
    bytes. `httpx.HTTPError` for a connection that failed or timed out."""
    request = client.build_request(
        "GET", url, params=params, headers=headers, extensions=extensions
    )
    response = await client.send(request, stream=True, follow_redirects=False)
    try:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > limit:
                raise TooLarge
        return Fetched(response.status_code, response.headers, bytes(body))
    finally:
        await response.aclose()
