"""What the page reader refuses, and what one page can't make it do (the rebuilt fetch):
reach this computer or its network by another spelling of an address, borrow another
site's connection or cookies, outgrow its caps by compressing, or outlast its deadline.

No network: HTTP goes through httpx's mock transport, names through a fake resolver.
"""

from __future__ import annotations

import asyncio
import gzip
import tracemalloc
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.adapters.web import fetch
from app.adapters.web.fetch import FetchRefused, Here, PageFetcher, is_public
from app.core.config_capabilities import CapabilitySettings
from app.services.connected.runtime import client_factory

PUBLIC = "93.184.215.14"
OTHER = "93.184.215.15"
HTML = (
    "<html><head><title>Trams</title></head><body><p>Tram 28 every ten minutes.</p></body></html>"
)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, text=HTML)


def _fetcher(
    handler: Callable[[httpx.Request], Any],
    names: dict[str, list[str]] | None = None,
    *,
    clients: list[httpx.AsyncClient] | None = None,
    **options: Any,
) -> PageFetcher:
    known = names or {"example.org": [PUBLIC], "other.example": [OTHER]}

    async def resolver(host: str, _port: int) -> list[str]:
        return known[host]

    def make() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        if clients is not None:
            clients.append(client)
        return client

    options.setdefault("max_bytes", 100_000)
    options.setdefault("place", lambda: Here(frozenset()))
    return PageFetcher(make, blocked=lambda: [], resolver=resolver, **options)


@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b::7f00:1",  # NAT64 around 127.0.0.1
        "64:ff9b::c0a8:101",  # NAT64 around 192.168.1.1
        "fec0::1",  # the old site-local block
        "::127.0.0.1",  # IPv4-compatible
        "::ffff:0:7f00:1",  # IPv4-translated
        "::ffff:8.8.8.8",  # any IPv4 written as IPv6
        "ff0e::1",  # global-scope multicast
    ],
)
def test_addresses_ipaddress_calls_global_are_still_refused(address: str) -> None:
    """(mutations: drop the NAT64 unwrap; drop the extra IPv6 blocks; allow mapped)"""
    assert not is_public(address)


def test_a_public_address_behind_nat64_is_judged_as_itself() -> None:
    assert is_public("64:ff9b::5db8:d70e")  # 93.184.215.14


# A home's IPv6 prefix as the provider gave it, this computer inside it, the router
# beside it on another /64 of the same /56, and a site outside it.
HOME_V6 = "2a10:5:5:ab01:1c2d:3e4f:5a6b:7c8d"
ROUTER_V6 = "2a10:5:5:ab00::1"
SITE_V6 = "2a10:5:5:ac00::80"


def _here(*addresses: str) -> Here:
    home = fetch.Here(frozenset(fetch.ipaddress.ip_address(a) for a in addresses))
    return Here(
        home.addresses,
        tuple(fetch.ipaddress.IPv6Network(f"{a}/56", strict=False) for a in addresses if ":" in a),
    )


async def test_this_computers_own_global_address_is_refused() -> None:
    """A page could reach a service listening on every interface through it."""
    fetcher = _fetcher(_ok, {"example.org": [PUBLIC]}, place=lambda: _here(PUBLIC))

    with pytest.raises(FetchRefused, match="your own network or this computer"):
        await fetcher.fetch("https://example.org/")


async def test_a_device_on_this_computers_ipv6_network_is_refused() -> None:
    """IPv6 has no NAT: the router beside her has a global address, in the /56 hers is
    in (mutation: check this computer's own addresses only)."""
    names = {"router.example": [ROUTER_V6], "site.example": [SITE_V6]}
    fetcher = _fetcher(_ok, names, place=lambda: _here(HOME_V6))

    with pytest.raises(FetchRefused, match="your own network or this computer"):
        await fetcher.fetch("https://router.example/")
    assert (await fetcher.fetch("https://site.example/")).text


def test_her_network_is_read_from_her_global_ipv6_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(mutations: keep a link-local or an IPv4 address's network; a /64 only)"""
    infos = [(0, 0, 0, "", (a, 0)) for a in (HOME_V6, "fe80::1%4", "192.168.1.20")]
    monkeypatch.setattr(fetch.socket, "getaddrinfo", lambda *_: infos)

    place = fetch.here()

    assert place.holds(ROUTER_V6) and place.holds("192.168.1.20") and place.holds("fe80::1")
    assert not place.holds(SITE_V6) and not place.holds("fe80::2")


async def test_where_she_is_is_read_again_for_every_hop() -> None:
    """Windows makes a new temporary IPv6 address every day; one kept for a run goes
    stale (mutation: read it once)."""
    reads: list[int] = []

    def place() -> Here:
        reads.append(1)
        return Here(frozenset())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.org/new"})
        return _ok(request)

    fetcher = _fetcher(handler, place=place)
    await fetcher.fetch("https://example.org/old")
    await fetcher.fetch("https://example.org/new")

    assert len(reads) == 3


@pytest.mark.parametrize(
    "url", ["https://example.org:8443/", "http://example.org:443/", "http://example.org:11434/"]
)
async def test_only_the_usual_web_ports_are_opened(url: str) -> None:
    """(mutation: drop the port check)"""
    with pytest.raises(FetchRefused) as refused:
        await _fetcher(_ok).fetch(url)

    assert refused.value.code == "host_not_allowed"


async def test_a_redirect_from_https_down_to_http_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://example.org/plain"})

    with pytest.raises(FetchRefused, match="from https to http"):
        await _fetcher(handler).fetch("https://example.org/")


async def test_each_hop_gets_its_own_client_and_no_cookie_follows_it() -> None:
    """A shared pool kept connections by address, so a second site on the same address
    reused one checked for the first; a shared jar sent one site's cookie to the next
    (mutations: reuse one client; keep cookies)."""
    clients: list[httpx.AsyncClient] = []
    sent: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie"))
        if request.headers["host"] == "example.org":
            return httpx.Response(
                302, headers={"location": "https://other.example/", "set-cookie": "id=secret"}
            )
        return _ok(request)

    page = await _fetcher(handler, clients=clients).fetch("https://example.org/")

    assert page.title == "Trams"
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert sent == [None, None]


def test_the_runtime_clients_ignore_proxies_and_keep_no_cookies() -> None:
    """Environment proxies sent HTTPS reads through a proxy that saw every address
    (mutations: trust the environment; use a normal jar)."""
    make = client_factory(CapabilitySettings(), httpx.create_ssl_context(trust_env=False))
    first, second = make(), make()

    assert not first.trust_env and first is not second
    first.cookies.set("id", "secret", domain="example.org")
    assert len(first.cookies.jar) == 0


async def test_a_compressed_page_cant_grow_past_the_cap() -> None:
    """97 KB of gzip is 100 MB of page (mutation: count bytes after httpx decodes)."""
    bomb = gzip.compress(b"\0" * 100_000_000, 9)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=httpx.ByteStream(bomb),
        )

    tracemalloc.start()
    try:
        with pytest.raises(FetchRefused) as refused:
            await _fetcher(handler, max_bytes=2_000_000).fetch("https://example.org/")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert refused.value.code == "fetch_too_large"
    # Refused is not enough: inflating a whole chunk before counting it took 0.5 GiB
    # first (mutation: decompress without a bound on each step).
    assert peak < 20_000_000


@pytest.mark.parametrize("encoding", ["br", "gzip, gzip", "zstd"])
async def test_an_encoding_not_asked_for_is_refused(encoding: str) -> None:
    """Brotli reaches over a million to one, and each stacked layer multiplies."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["accept-encoding"])
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": encoding},
            stream=httpx.ByteStream(b"x"),
        )

    with pytest.raises(FetchRefused, match="form I can't read"):
        await _fetcher(handler).fetch("https://example.org/")

    assert seen == ["gzip, deflate"]


async def test_a_gzipped_page_is_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(HTML.encode())),
        )

    page = await _fetcher(handler).fetch("https://example.org/")

    assert page.text == "Tram 28 every ten minutes."


async def test_a_page_that_never_finishes_is_given_up_on() -> None:
    """(mutation: no overall deadline)"""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return _ok(request)

    with pytest.raises(FetchRefused, match="took too long"):
        await _fetcher(handler, deadline_s=0.1).fetch("https://example.org/")


@pytest.mark.parametrize(
    ("label", "expected"),
    [("latin-1", "Café"), ("l1", "Café"), ("punycode", "Caf�"), ("base64", "Caf�")],
)
async def test_a_charset_is_read_only_if_it_is_a_text_encoding(label: str, expected: str) -> None:
    """Any codec Python knows was accepted; some decode in quadratic time, and some
    aren't text (mutation: accept any codec)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = "Café".encode("latin-1")
        return httpx.Response(
            200, headers={"content-type": f"text/plain; charset={label}"}, content=body
        )

    page = await _fetcher(handler).fetch("https://example.org/a.txt")

    assert page.text == expected
