"""The search providers (`adapters/web/searxng.py`, `brave.py`): their answers read into
hits, and every failure said as a reason the app has words for, never the provider's
own text. No network: httpx's mock transport answers.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.adapters.web.brave import ENDPOINT, BraveProvider
from app.adapters.web.search import SearchFailed
from app.adapters.web.searxng import SearxngProvider

SEARXNG_ANSWER = {
    "query": "trams lisbon",
    "results": [
        {
            "title": "Tram 28 <b>guide</b>",
            "url": "https://example.org/28",
            "content": "Runs &amp; stops.",
        },
        {"title": "No link", "content": "skipped"},
        {"title": "Not the web", "url": "javascript:alert(1)", "content": "skipped"},
        {"title": "Fares", "url": "https://example.org/fares"},
    ],
}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_searxng_asks_for_json_and_reads_its_results() -> None:
    """(mutations: drop `format=json`; keep the markup; keep a result with no link)"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SEARXNG_ANSWER)

    hits = await SearxngProvider(_client(handler), "http://127.0.0.1:8888/").search(
        "trams lisbon", 5
    )

    [request] = seen
    assert request.url.path == "/search"
    assert request.url.params["q"] == "trams lisbon" and request.url.params["format"] == "json"
    assert [(hit.title, hit.url, hit.snippet) for hit in hits] == [
        ("Tram 28 guide", "https://example.org/28", "Runs & stops."),
        ("Fares", "https://example.org/fares", ""),
    ]


async def test_searxng_stops_at_the_count() -> None:
    hits = await SearxngProvider(
        _client(lambda r: httpx.Response(200, json=SEARXNG_ANSWER)), "http://s"
    ).search("q", 1)

    assert len(hits) == 1


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(403, text="Forbidden"), "json_off"),
        (httpx.Response(500, text="boom"), "refused"),
        (httpx.Response(429, text="slow down"), "rate_limited"),
        (httpx.Response(200, text="<html>not json</html>"), "bad_answer"),
        (httpx.Response(200, json=["a", "list"]), "bad_answer"),
        (httpx.Response(200, json={"results": "nope"}), "bad_answer"),
    ],
)
async def test_searxng_says_why_it_failed(response: httpx.Response, reason: str) -> None:
    with pytest.raises(SearchFailed) as failed:
        await SearxngProvider(_client(lambda r: response), "http://s").search("q", 5)

    assert failed.value.reason == reason


async def test_a_searxng_that_isnt_running_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SearchFailed) as failed:
        await SearxngProvider(_client(handler), "http://127.0.0.1:8888").search("q", 5)

    assert failed.value.reason == "unreachable"


async def test_brave_sends_its_key_in_the_header_and_reads_web_results() -> None:
    """(mutations: send the key as a parameter; ask for more than 20)"""
    seen: list[httpx.Request] = []
    answer = {
        "web": {
            "results": [
                {
                    "title": "Fares",
                    "url": "https://example.org/f",
                    "description": "3 <strong>euros</strong>",
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer)

    hits = await BraveProvider(_client(handler), "sk-brave-FAKE-0000").search("fares", 50)

    [request] = seen
    assert str(request.url).startswith(ENDPOINT)
    assert request.headers["X-Subscription-Token"] == "sk-brave-FAKE-0000"
    assert "sk-brave-FAKE-0000" not in str(request.url)
    assert request.url.params["count"] == "20"
    assert [(hit.title, hit.snippet) for hit in hits] == [("Fares", "3 euros")]


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(401, text="bad key"), "bad_key"),
        (httpx.Response(403, text="bad key"), "bad_key"),
        (httpx.Response(429, text="quota"), "rate_limited"),
        (httpx.Response(200, text="nope"), "bad_answer"),
    ],
)
async def test_brave_says_why_it_failed(response: httpx.Response, reason: str) -> None:
    with pytest.raises(SearchFailed) as failed:
        await BraveProvider(_client(lambda r: response), "k").search("q", 5)

    assert failed.value.reason == reason


async def test_brave_with_no_web_results_has_none() -> None:
    hits = await BraveProvider(
        _client(lambda r: httpx.Response(200, json={"type": "search"})), "k"
    ).search("q", 5)

    assert hits == []
