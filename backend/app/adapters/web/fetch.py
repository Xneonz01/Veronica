"""One web page for `web.read`, and the addresses that may never be fetched.

A page can link anywhere, so the model can be led to ask for any address: the user's
router, a service on this computer, her own backend. Every request is checked first:
http or https, no credentials in it, a host the user hasn't blocked, and every address
the host resolves to public. The request then goes to the address that was checked, not
to the name again, so a DNS answer that changes between the check and the connection
can't slip a private one in; TLS still verifies the certificate against the name
(httpcore's `sni_hostname`). A redirect is checked the same way, three at most.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.adapters.web.capped import Fetched, TooLarge, get_capped
from app.adapters.web.page_text import readable

Resolver = Callable[[str, int], Awaitable[list[str]]]

_REDIRECTS = 3
_REDIRECTED = frozenset({301, 302, 303, 307, 308})
_PAGE_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
_ACCEPT = "text/html, application/xhtml+xml, text/plain;q=0.9"


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
    """Whether an address is on the internet: not this computer, not a private or
    link-local network, not reserved or multicast. An IPv4 address written as IPv6
    (`::ffff:127.0.0.1`) is judged as the IPv4 one."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to, in order, once each."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class PageFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_bytes: int,
        blocked: Callable[[], Collection[str]],
        resolver: Resolver = resolve,
    ) -> None:
        self._client = client
        self._max_bytes = max_bytes
        self._blocked = blocked
        self._resolve = resolver

    async def fetch(self, url: str) -> Page:
        current = url
        for _hop in range(_REDIRECTS + 1):
            target = self._target(current)
            address = await self._public_address(target)
            fetched = await self._get(target, address)
            if fetched.status in _REDIRECTED:
                location = fetched.headers.get("location")
                if not location:
                    raise FetchRefused("fetch_failed", "That page couldn't be opened.")
                current = urljoin(str(target), location)
                continue
            if fetched.status >= 400:
                raise FetchRefused("fetch_failed", "That page couldn't be opened.")
            return _page(str(target), fetched.headers, fetched.body)
        raise FetchRefused("fetch_failed", "That page redirected too many times.")

    def _target(self, url: str) -> httpx.URL:
        try:
            target = httpx.URL(url)
        except httpx.InvalidURL:
            raise FetchRefused("fetch_failed", "That isn't a web address.") from None
        if target.scheme not in ("http", "https") or not target.host:
            raise FetchRefused("host_not_allowed", "I only open http and https pages.")
        if target.userinfo:
            raise FetchRefused(
                "host_not_allowed", "I don't open addresses with a password in them."
            )
        host = target.host.lower().rstrip(".")
        if any(host == name or host.endswith(f".{name}") for name in self._blocked()):
            raise FetchRefused("host_not_allowed", "That site is blocked in Settings.")
        return target

    async def _public_address(self, target: httpx.URL) -> str:
        port = target.port or (443 if target.scheme == "https" else 80)
        try:
            addresses = await self._resolve(target.host, port)
        except OSError:
            raise FetchRefused("fetch_failed", "I couldn't find that site.") from None
        if not addresses or not all(is_public(address) for address in addresses):
            raise FetchRefused(
                "host_not_allowed",
                "That address is on your own network or this computer, so I won't open it.",
            )
        return addresses[0]

    async def _get(self, target: httpx.URL, address: str) -> Fetched:
        host = target.host if target.port is None else f"{target.host}:{target.port}"
        extensions: dict[str, object] = {}
        if target.scheme == "https":
            extensions["sni_hostname"] = target.host
        try:
            return await get_capped(
                self._client,
                target.copy_with(host=address),
                limit=self._max_bytes,
                headers={"Host": host, "Accept": _ACCEPT},
                extensions=extensions,
            )
        except TooLarge:
            size = self._max_bytes // 1000
            raise FetchRefused("fetch_too_large", f"That page is over {size} KB.") from None
        except httpx.HTTPError:
            raise FetchRefused("fetch_failed", "I couldn't reach that page.") from None


def _page(url: str, headers: httpx.Headers, body: bytes) -> Page:
    kind, _, params = headers.get("content-type", "").partition(";")
    if kind.strip().lower() not in _PAGE_TYPES:
        raise FetchRefused("not_a_page", "That link isn't a web page I can read.")
    text = body.decode(_charset(params), errors="replace")
    if kind.strip().lower() == "text/plain":
        return Page(url, "", text.strip())
    title, readable_text = readable(text)
    return Page(url, title, readable_text)


def _charset(params: str) -> str:
    for part in params.split(";"):
        name, _, value = part.strip().partition("=")
        if name.lower() == "charset" and value:
            charset = value.strip().strip('"')
            try:
                "".encode(charset)
            except LookupError:
                break
            return charset
    return "utf-8"
