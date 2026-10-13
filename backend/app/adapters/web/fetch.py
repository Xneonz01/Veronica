"""One web page for `web.read`, and the addresses that may never be fetched.

A page can link anywhere, so the model can be led to ask for any address: the user's
router, a service on this computer, her own backend. Every request is checked first:
http or https on its own port, no credentials in it, a host the user hasn't blocked, and
every address the host resolves to on the internet (`is_public`) and neither this
computer nor the IPv6 network it is on (`Here`). The request then goes
to the address that was checked, not to the name again, so a DNS answer that changes
between the check and the connection can't slip a private one in; TLS still verifies
the certificate against the name (httpcore's `sni_hostname`). A redirect is checked the
same way, three at most, and never from https down to http.

Each hop is a client of its own (`ClientFactory`): no connection, cookie or proxy is
shared between sites. A shared pool kept connections by address, so a second site on the
same address reused one whose certificate was checked for the first.
"""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.adapters.web.capped import BadEncoding, Fetched, TooLarge, get_capped
from app.adapters.web.page_text import readable
from app.core.text_safety import clean_untrusted

Resolver = Callable[[str, int], Awaitable[list[str]]]
ClientFactory = Callable[[], httpx.AsyncClient]

_REDIRECTS = 3
_REDIRECTED = frozenset({301, 302, 303, 307, 308})
_PAGE_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
_ACCEPT = "text/html, application/xhtml+xml, text/plain;q=0.9"
_PORTS = {"http": 80, "https": 443}
# Codecs a page may name, by the name Python's codec registry gives each label ("latin-1",
# "l1" and "iso-8859-1" are all iso8859-1). Any codec it knows was accepted, and some
# decode in time that grows with the square of the page (`punycode`), or aren't text at
# all (`base64`, `uu`); a label for anything else is read as UTF-8.
_CODECS = frozenset(
    {"utf-8", "utf-16", "ascii", "koi8-r", "koi8-u", "shift_jis", "euc_jp", "gb18030",
     "gbk", "gb2312", "big5", "euc_kr"}
    | {f"iso8859-{n}" for n in (*range(1, 11), 13, 14, 15, 16)}
    | {f"cp{n}" for n in range(1250, 1259)}
)  # fmt: skip
# IPv6 ranges `ipaddress` calls global on Python 3.12 that reach no public host: the old
# site-local block, IPv4-compatible and IPv4-translated forms.
_NOT_PUBLIC_V6 = tuple(
    ipaddress.IPv6Network(net) for net in ("fec0::/10", "::/96", "::ffff:0:0:0/96")
)
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
# Around this computer's global IPv6 addresses, the network it is on: IPv6 has no NAT, so
# the router, a printer or a NAS beside her has a global address too. A /56 is what most
# homes are given, and holds every /64 a home router hands out.
_HOME_PREFIX = 56


class FetchRefused(Exception):
    """Why a page wasn't read: a kernel code and a sentence fit to show."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    title: str
    text: str


def is_public(address: str) -> bool:
    """Whether an address is in the internet's ranges: not loopback, not a private or
    link-local network, not reserved or multicast. An IPv4 address carried inside an
    IPv6 one (mapped, or behind NAT64) is judged as that IPv4 address. Whether it is
    this computer, or beside it, is `Here`'s question."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return False  # an IPv4 address written as IPv6 is never needed to reach one
        if ip in _NAT64:
            return is_public(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
        if any(ip in net for net in _NOT_PUBLIC_V6):
            return False
    return ip.is_global and not ip.is_multicast


@dataclass(frozen=True, slots=True)
class Here:
    """This computer's own addresses, and the IPv6 networks its global ones are in."""

    addresses: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]
    networks: tuple[ipaddress.IPv6Network, ...] = ()

    def holds(self, address: str) -> bool:
        ip = ipaddress.ip_address(address)
        return ip in self.addresses or any(ip in network for network in self.networks)


def here() -> Here:
    """Read for every request, never kept: Windows gives this computer a new temporary
    IPv6 address every day. Best effort: the public address a router shares with every
    computer behind it isn't known here."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None)
    except OSError:
        return Here(frozenset())
    addresses = frozenset(ipaddress.ip_address(str(info[4][0]).split("%")[0]) for info in infos)
    networks = {
        ipaddress.IPv6Network(f"{ip}/{_HOME_PREFIX}", strict=False)
        for ip in addresses
        if isinstance(ip, ipaddress.IPv6Address) and ip.is_global
    }
    return Here(addresses, tuple(networks))


async def resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to, in order, once each."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class PageFetcher:
    def __init__(
        self,
        clients: ClientFactory,
        *,
        max_bytes: int,
        blocked: Callable[[], Collection[str]],
        resolver: Resolver = resolve,
        deadline_s: float = 25.0,
        place: Callable[[], Here] = here,
    ) -> None:
        self._clients = clients
        self._max_bytes = max_bytes
        self._blocked = blocked
        self._resolve = resolver
        self._deadline_s = deadline_s
        self._place = place

    async def fetch(self, url: str) -> Page:
        try:
            async with asyncio.timeout(self._deadline_s):
                target, fetched = await self._follow(url)
        except TimeoutError:
            raise FetchRefused("fetch_failed", "That page took too long to open.") from None
        return await asyncio.to_thread(_page, str(target), fetched.headers, fetched.body)

    async def _follow(self, url: str) -> tuple[httpx.URL, Fetched]:
        current, secure = url, False
        for _hop in range(_REDIRECTS + 1):
            target = self._target(current)
            if secure and target.scheme == "http":
                raise FetchRefused("host_not_allowed", "That page sent me from https to http.")
            secure = target.scheme == "https"
            fetched = await self._get(target, await self._public_address(target))
            if fetched.status in _REDIRECTED:
                location = fetched.headers.get("location")
                if not location:
                    raise FetchRefused("fetch_failed", "That page couldn't be opened.")
                current = urljoin(str(target), location)
                continue
            if fetched.status >= 400:
                raise FetchRefused("fetch_failed", "That page couldn't be opened.")
            return target, fetched
        raise FetchRefused("fetch_failed", "That page redirected too many times.")

    def _target(self, url: str) -> httpx.URL:
        try:
            target = httpx.URL(url)
        except (httpx.InvalidURL, UnicodeError):
            raise FetchRefused("fetch_failed", "That isn't a web address.") from None
        if target.scheme not in _PORTS or not target.raw_host:
            raise FetchRefused("host_not_allowed", "I only open http and https pages.")
        if target.userinfo:
            raise FetchRefused(
                "host_not_allowed", "I don't open addresses with a password in them."
            )
        if target.port is not None and target.port != _PORTS[target.scheme]:
            raise FetchRefused("host_not_allowed", "I only open pages on the usual web ports.")
        names = {
            target.raw_host.decode("ascii").lower().rstrip("."),
            target.host.lower().rstrip("."),
        }
        for name in self._blocked():
            if any(host == name or host.endswith(f".{name}") for host in names):
                raise FetchRefused("host_not_allowed", "That site is blocked in Settings.")
        return target

    async def _public_address(self, target: httpx.URL) -> str:
        host = target.raw_host.decode("ascii")
        try:
            addresses = await self._resolve(host, _PORTS[target.scheme])
        except OSError:
            raise FetchRefused("fetch_failed", "I couldn't find that site.") from None
        place = await asyncio.to_thread(self._place)  # a lookup of its own: off the loop
        if not addresses or not all(is_public(a) and not place.holds(a) for a in addresses):
            raise FetchRefused(
                "host_not_allowed",
                "That address is on your own network or this computer, so I won't open it.",
            )
        return addresses[0]

    async def _get(self, target: httpx.URL, address: str) -> Fetched:
        host = target.raw_host.decode("ascii")
        extensions: dict[str, object] = {}
        if target.scheme == "https":
            extensions["sni_hostname"] = host
        try:
            async with self._clients() as client:
                return await get_capped(
                    client,
                    target.copy_with(host=address),
                    limit=self._max_bytes,
                    headers={"Host": host, "Accept": _ACCEPT},
                    extensions=extensions,
                )
        except TooLarge:
            size = self._max_bytes // 1000
            raise FetchRefused("fetch_too_large", f"That page is over {size} KB.") from None
        except BadEncoding:
            raise FetchRefused("fetch_failed", "That page came in a form I can't read.") from None
        except httpx.HTTPError:
            raise FetchRefused("fetch_failed", "I couldn't reach that page.") from None


def _page(url: str, headers: httpx.Headers, body: bytes) -> Page:
    """The page as text: run off the event loop, since a page is up to 2 MB."""
    kind, _, params = headers.get("content-type", "").partition(";")
    if kind.strip().lower() not in _PAGE_TYPES:
        raise FetchRefused("not_a_page", "That link isn't a web page I can read.")
    text = body.decode(_charset(params), errors="replace")
    if kind.strip().lower() == "text/plain":
        return Page(url, "", clean_untrusted(text).strip())
    title, readable_text = readable(text)
    return Page(url, title, readable_text)


def _charset(params: str) -> str:
    for part in params.split(";"):
        name, _, value = part.strip().partition("=")
        if name.lower() == "charset" and value:
            try:
                codec = codecs.lookup(value.strip().strip('"')).name
            except LookupError:
                return "utf-8"
            return codec if codec in _CODECS else "utf-8"
    return "utf-8"
