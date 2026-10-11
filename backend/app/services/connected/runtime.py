"""Her reach to the web: the live settings, one HTTP client and what uses it (ADR 0003).

One client for every request, built once and closed at shutdown, never following a
redirect on its own: the page fetcher checks each one (`adapters/web/fetch.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def build_connected(
    limits: CapabilitySettings, bus: EventBus, data_dir: Path, data_key: bytes | None
) -> ConnectedRuntime:
    store = ConnectedStore(bus, data_dir / "connected.json", data_key)
    store.load()
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(limits.web_timeout_s),
        headers={"User-Agent": _USER_AGENT},
        follow_redirects=False,
    )
    fetcher = PageFetcher(client, max_bytes=limits.web_max_page_bytes, blocked=store.blocked)
    return ConnectedRuntime(store, client, fetcher)
