"""Reading one web page (`adapters/web/fetch.py`): only public addresses, the address
that was checked is the one connected to, and a page is bounded and read as text.

No network: HTTP goes through httpx's mock transport, names through a fake resolver.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.adapters.web.fetch import FetchRefused, PageFetcher, is_public
from app.adapters.web.page_text import readable

PUBLIC = "93.184.215.14"
HTML = (
    "<html><head><title>Trams in Lisbon</title><style>p{color:red}</style></head>"
    "<body><nav>Home | About</nav><h1>Timetable</h1><p>Tram 28 runs every ten minutes.</p>"
    "<script>steal()</script><p>Fares are 3 euros.</p></body></html>"
)


def _fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
    addresses: dict[str, list[str]] | None = None,
    *,
    blocked: list[str] | None = None,
    max_bytes: int = 100_000,
) -> PageFetcher:
    names = addresses or {"example.org": [PUBLIC]}

    async def resolver(host: str, _port: int) -> list[str]:
        if host not in names:
            raise OSError("no such host")
        return names[host]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PageFetcher(
        client, max_bytes=max_bytes, blocked=lambda: blocked or [], resolver=resolver
    )


def _html(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("192.168.1.1", False),
        ("172.16.4.2", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        (PUBLIC, True),
        ("2606:4700:4700::1111", True),
    ],
)
def test_only_an_address_on_the_internet_is_public(address: str, public: bool) -> None:
    """(mutation: judge a mapped IPv6 address as IPv6)"""
    assert is_public(address) is public


async def test_a_page_is_fetched_from_the_checked_address_and_read_as_text() -> None:
    """Connected to the IP the check passed, the name kept for Host and TLS (mutations:
    connect by name; drop the SNI name)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _html(request)

    page = await _fetcher(handler).fetch("https://example.org/trams")

    [request] = seen
    assert request.url.host == PUBLIC and request.headers["host"] == "example.org"
    assert request.extensions.get("sni_hostname") == "example.org"
    assert page.url == "https://example.org/trams" and page.title == "Trams in Lisbon"
    assert "Tram 28 runs every ten minutes." in page.text and "Fares are 3 euros." in page.text
    assert "steal" not in page.text and "color:red" not in page.text and "About" not in page.text


@pytest.mark.parametrize(
    ("url", "names"),
    [
        ("http://localhost:8000/health", {"localhost": ["127.0.0.1"]}),
        ("http://192.168.1.1/admin", {"192.168.1.1": ["192.168.1.1"]}),
        ("http://rebind.example/", {"rebind.example": [PUBLIC, "10.0.0.2"]}),
        ("http://[::1]:8000/", {"::1": ["::1"]}),
    ],
)
async def test_an_address_on_this_computer_or_its_network_is_refused(
    url: str, names: dict[str, list[str]]
) -> None:
    """Every address the name has must be public, not the first (mutation: any)."""
    fetcher = _fetcher(_html, names)

    with pytest.raises(FetchRefused) as refused:
        await fetcher.fetch(url)

    assert refused.value.code == "host_not_allowed"


async def test_a_redirect_to_a_private_address_is_refused() -> None:
    """Checked again on every hop (mutation: check the first URL only)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://router.lan/admin"})

    fetcher = _fetcher(handler, {"example.org": [PUBLIC], "router.lan": ["192.168.1.1"]})

    with pytest.raises(FetchRefused) as refused:
        await fetcher.fetch("http://example.org/go")

    assert refused.value.code == "host_not_allowed"


async def test_a_redirect_is_followed_three_times_at_most() -> None:
    hops: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(request.url.path)
        return httpx.Response(301, headers={"location": f"/{len(hops)}"})

    with pytest.raises(FetchRefused) as refused:
        await _fetcher(handler).fetch("http://example.org/start")

    assert refused.value.code == "fetch_failed" and len(hops) == 4


@pytest.mark.parametrize(
    ("url", "blocked", "code"),
    [
        ("ftp://example.org/file", [], "host_not_allowed"),
        ("https://user:secret@example.org/", [], "host_not_allowed"),
        ("https://example.org/", ["example.org"], "host_not_allowed"),
        ("https://news.example.org/", ["example.org"], "host_not_allowed"),
        ("https://unknown.invalid/", [], "fetch_failed"),
        ("not a url at all", [], "host_not_allowed"),
    ],
)
async def test_what_is_never_fetched(url: str, blocked: list[str], code: str) -> None:
    """(mutations: allow another scheme; ignore credentials; match a blocked host exactly)"""
    names = {"example.org": [PUBLIC], "news.example.org": [PUBLIC]}

    with pytest.raises(FetchRefused) as refused:
        await _fetcher(_html, names, blocked=blocked).fetch(url)

    assert refused.value.code == code


async def test_a_page_past_the_limit_is_cut_off() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"a" * 5000)

    with pytest.raises(FetchRefused) as refused:
        await _fetcher(handler, max_bytes=1000).fetch("https://example.org/")

    assert refused.value.code == "fetch_too_large"


async def test_a_file_that_isnt_a_page_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    with pytest.raises(FetchRefused) as refused:
        await _fetcher(handler).fetch("https://example.org/doc.pdf")

    assert refused.value.code == "not_a_page"


async def test_plain_text_is_read_as_it_is_in_its_charset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = "Café closes at 18:00.".encode("latin-1")
        return httpx.Response(
            200, headers={"content-type": "text/plain; charset=latin-1"}, content=body
        )

    page = await _fetcher(handler).fetch("https://example.org/hours.txt")

    assert page.text == "Café closes at 18:00." and page.title == ""


def test_html_keeps_blocks_apart_and_drops_what_isnt_read() -> None:
    title, text = readable(
        "<h2>Hours</h2><p>Mon&ndash;Fri</p><p>9 &amp; 5</p><template>x</template>"
    )

    assert title == "" and text.split("\n") == ["Hours", "", "Mon–Fri", "", "9 & 5"]
