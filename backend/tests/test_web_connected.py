"""Web search in Veronica (ADR 0003): off until switched on, live when it is, the key kept
sealed, and her prompt saying what leaves the computer only while something does.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.web.fetch import FetchRefused, Page
from app.adapters.web.search import SearchFailed, SearchHit
from app.api.http_guards import install as install_guards
from app.api.routes_connected import router
from app.capabilities.web import register_web
from app.core.event_bus import InProcessEventBus
from app.kernel.registry import CapabilityRegistry
from app.schemas.events import Event
from app.services.connected.runtime import ConnectedRuntime
from app.services.connected.store import CannotKeepKey, ConnectedSettings, ConnectedStore
from app.services.persona.defaults import DEFAULT_PERSONALITY, build_prompt

KEY = "sk-brave-FAKE-1234567890"
DATA_KEY = os.urandom(32)


class _Provider:
    name = "fake"

    def __init__(self, hits: list[SearchHit] | None = None, fails: str | None = None) -> None:
        self.hits = hits or []
        self.fails = fails
        self.queries: list[str] = []

    async def search(self, query: str, count: int) -> list[SearchHit]:
        self.queries.append(query)
        if self.fails:
            raise SearchFailed(self.fails)  # type: ignore[arg-type]
        return self.hits[:count]


class _Fetcher:
    def __init__(self, page: Page | None = None, refused: FetchRefused | None = None) -> None:
        self.page, self.refused = page, refused

    async def fetch(self, url: str) -> Page:
        if self.refused:
            raise self.refused
        assert self.page is not None
        return self.page


def _store(tmp_path: Path, data_key: bytes | None = DATA_KEY) -> tuple[ConnectedStore, list[Event]]:
    bus = InProcessEventBus()
    seen: list[Event] = []

    async def note(event: Event) -> None:
        seen.append(event)

    bus.subscribe("connected.changed", note)
    store = ConnectedStore(bus, tmp_path / "connected.json", data_key)
    store.load()
    return store, seen


@dataclass(slots=True)
class _Runtime(ConnectedRuntime):
    """The real runtime with a provider of the test's choosing."""

    fixed: Any = None

    def provider(self) -> Any:
        return self.fixed


def _runtime(store: ConnectedStore, provider: Any = None, fetcher: Any = None) -> ConnectedRuntime:
    return _Runtime(store, httpx.AsyncClient(), fetcher or _Fetcher(), provider)


async def _on(store: ConnectedStore) -> None:
    await store.update(ConnectedSettings(web_search=True))


async def test_it_starts_off_and_a_change_is_saved_and_announced(tmp_path: Path) -> None:
    """(mutations: on by default; skip the event; skip the save)"""
    store, seen = _store(tmp_path)
    assert not store.web_on()

    await store.update(ConnectedSettings(web_search=True, blocked_hosts=[" Ads.Example.COM. ", ""]))

    again, _ = _store(tmp_path)
    assert again.web_on() and again.blocked() == ["ads.example.com"]
    assert [event.payload["web_search"] for event in seen] == [True]


async def test_the_key_is_kept_sealed_and_never_shown(tmp_path: Path) -> None:
    """(mutations: save it plain; return it in the view)"""
    store, seen = _store(tmp_path)

    await store.set_key(KEY)

    saved = (tmp_path / "connected.json").read_text(encoding="utf-8")
    assert KEY not in saved and KEY not in json.dumps(store.view()) and KEY not in str(seen)
    assert store.view()["brave_key_set"] is True
    reopened, _ = _store(tmp_path)
    assert reopened.brave_key() == KEY
    elsewhere, _ = _store(tmp_path, data_key=os.urandom(32))  # another account's key
    assert elsewhere.brave_key() is None


async def test_without_a_data_key_no_key_is_kept(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, data_key=None)

    with pytest.raises(CannotKeepKey):
        await store.set_key(KEY)


def test_a_damaged_file_leaves_everything_off(tmp_path: Path) -> None:
    (tmp_path / "connected.json").write_text("{not json", encoding="utf-8")

    store, _ = _store(tmp_path)

    assert not store.web_on()


async def test_the_tools_are_offered_only_while_web_search_is_on(tmp_path: Path) -> None:
    """Registered once, offered live (mutation: offer them regardless)."""
    store, _ = _store(tmp_path)
    registry = CapabilityRegistry()
    register_web(registry, _runtime(store), results=5)

    assert [cap.name for cap in registry.offered(call=False)] == []
    await _on(store)
    assert [cap.name for cap in registry.offered(call=False)] == ["web.search", "web.read"]
    assert [cap.name for cap in registry.offered(call=True)] == ["web.search"]


async def test_a_search_while_off_is_refused(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    provider = _Provider()
    registry = CapabilityRegistry()
    register_web(registry, _runtime(store, provider), results=5)

    result = await registry.get("web.search").handler(_args(registry, "web.search", query="trams"))  # type: ignore[union-attr]

    assert result.code == "network_disabled" and provider.queries == []


async def test_a_search_gives_numbered_results_as_quoted_text(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    await _on(store)
    hits = [SearchHit("Tram 28", "https://example.org/28", "Every ten minutes.")]
    registry = CapabilityRegistry()
    register_web(registry, _runtime(store, _Provider(hits)), results=5)

    result = await registry.get("web.search").handler(_args(registry, "web.search", query="trams"))  # type: ignore[union-attr]

    assert (
        result.ok
        and result.data["content"] == "[1] Tram 28\nhttps://example.org/28\nEvery ten minutes."
    )
    assert result.detail == "Searched the web: 1 result."


async def test_a_failed_search_says_why_in_her_words(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    await _on(store)
    registry = CapabilityRegistry()
    register_web(registry, _runtime(store, _Provider(fails="json_off")), results=5)

    result = await registry.get("web.search").handler(_args(registry, "web.search", query="q"))  # type: ignore[union-attr]

    assert result.code == "search_unavailable" and "JSON" in result.detail


async def test_a_page_is_read_and_a_refusal_keeps_its_code(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    await _on(store)
    page = Page("https://example.org/28", "Tram 28", "Every ten minutes.")
    registry = CapabilityRegistry()
    register_web(registry, _runtime(store, fetcher=_Fetcher(page)), results=5)
    refusing = CapabilityRegistry()
    refused = FetchRefused("host_not_allowed", "That address is on your own network.")
    register_web(refusing, _runtime(store, fetcher=_Fetcher(refused=refused)), results=5)

    read = await registry.get("web.read").handler(_args(registry, "web.read", url=page.url))  # type: ignore[union-attr]
    blocked = await refusing.get("web.read").handler(
        _args(refusing, "web.read", url="http://10.0.0.1")
    )  # type: ignore[union-attr]

    assert read.ok and read.data["content"].startswith("Tram 28\nhttps://example.org/28\n\n")
    assert read.detail == "Read a page on example.org."
    assert blocked.code == "host_not_allowed"


def test_her_prompt_says_what_leaves_the_computer_only_while_web_search_is_on() -> None:
    """(mutations: keep the old promise; offer a call the page reader; with no tools)"""
    off = build_prompt(DEFAULT_PERSONALITY, spoken=False, tools_enabled=True)
    chat = build_prompt(DEFAULT_PERSONALITY, spoken=False, tools_enabled=True, web=True)
    call = build_prompt(DEFAULT_PERSONALITY, spoken=True, tools_enabled=True, web=True)
    toolless = build_prompt(DEFAULT_PERSONALITY, spoken=False, tools_enabled=False, web=True)

    assert "Everything stays on this computer." in off and "web_search" not in off
    assert "except what you search the web for" in chat and "web_read" in chat
    assert "web_search" in call and "web_read" not in call
    assert "web_search" not in toolless and "Everything stays on this computer." in toolless


async def test_the_api_never_returns_the_key_and_refuses_a_bad_address(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    app = FastAPI()
    install_guards(app)
    app.include_router(router)
    app.state.connected = _runtime(store, _Provider([SearchHit("A", "https://a.example", "")]))
    client = TestClient(app)

    assert client.put("/connected/key", json={"key": KEY}).json()["brave_key_set"] is True
    assert KEY not in client.get("/connected").text
    assert client.put("/connected", json={"searxng_url": "file:///etc"}).status_code == 422
    turned = client.put("/connected", json={"web_search": True, "provider": "searxng"})
    assert turned.json()["web_search"] is True and store.web_on()
    assert client.post("/connected/test").json() == {"ok": True, "results": 1}


def _args(registry: CapabilityRegistry, name: str, **values: str) -> Any:
    capability = registry.get(name)
    assert capability is not None
    return capability.args_model.model_validate(values)
