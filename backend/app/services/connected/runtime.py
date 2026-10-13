"""Her reach to the web: the live settings, the HTTP clients and what uses them (ADR 0003).

The search provider has one client, built once and closed at shutdown. A page read gets
a client of its own for every hop (`adapters/web/fetch.py`), so nothing a site leaves
behind (a connection, a cookie) reaches another. None of them follows a redirect on its
own, reads a proxy from the environment or keeps cookies, and they share one TLS
context, built once: making one per client loads every certificate again, 128 ms on the
event loop that calls run on.
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any

import httpx

from app.adapters.web.brave import BraveProvider
from app.adapters.web.fetch import PageFetcher
from app.adapters.web.search import SearchProvider
from app.adapters.web.searxng import SearxngProvider
from app.core.config_capabilities import CapabilitySettings
from app.core.event_bus import EventBus
from app.services.connected.store import ConnectedStore

_USER_AGENT = "Mozilla/5.0 (compatible; Veronica)"


@dataclass(slots=True)
class ConnectedRuntime:
    store: ConnectedStore
    client: httpx.AsyncClient
    fetcher: PageFetcher

    def provider(self) -> SearchProvider | None:
        """The search provider the settings name; None for Brave without a key."""
        settings = self.store.settings
        if settings.provider == "brave":
            key = self.store.brave_key()
            return BraveProvider(self.client, key) if key else None
        return SearxngProvider(self.client, settings.searxng_url)

    async def aclose(self) -> None:
        await self.client.aclose()


class _NoCookies(CookieJar):
    """A jar that keeps nothing: no site, and no search provider, can set a cookie."""

    def set_cookie(self, cookie: Cookie) -> None:
        return None

    def extract_cookies(self, response: Any, request: Any) -> None:
        return None


def client_factory(
    limits: CapabilitySettings, tls: ssl.SSLContext
) -> Callable[[], httpx.AsyncClient]:
    """A fresh client each call: no keep-alive, no cookies, no proxy, no redirects."""

    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(limits.web_timeout_s),
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=False,
            trust_env=False,
            verify=tls,
            cookies=_NoCookies(),
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    return make


def build_connected(
    limits: CapabilitySettings, bus: EventBus, data_dir: Path, data_key: bytes | None
) -> ConnectedRuntime:
    store = ConnectedStore(bus, data_dir / "connected.json", data_key)
    store.load()
    clients = client_factory(limits, httpx.create_ssl_context(trust_env=False))
    fetcher = PageFetcher(clients, max_bytes=limits.web_max_page_bytes, blocked=store.blocked)
    return ConnectedRuntime(store, clients(), fetcher)
