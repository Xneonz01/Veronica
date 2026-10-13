"""What a page or a search result becomes before the model reads it, and what a failure
leaves in the log: linear in the page, nothing invisible, no key or argument quoted.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.adapters.web.brave import BraveProvider
from app.adapters.web.page_text import readable
from app.adapters.web.search import SearchFailed, hits_from
from app.api.http_guards import install as install_guards
from app.api.routes_connected import KeyUpdate
from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityRequest, CapabilityResult, RiskTier
from app.kernel.confirm import ConfirmationBroker
from app.kernel.kernel import CapabilityKernel
from app.kernel.policy import PolicyStore
from app.kernel.registry import Capability, CapabilityRegistry

SECRET = "sk-proj-FAKE0000000000000000fake0000000000"


def _seconds(text: str) -> float:
    started = time.perf_counter()
    readable(text)
    return time.perf_counter() - started


@pytest.mark.parametrize("unit", ["<a ", "<a href='", '<x="', "<", "<!--", "&", "<b>x</b>"])
def test_reading_a_page_takes_time_in_proportion_to_its_length(unit: str) -> None:
    """Twice the page, about twice the time: the standard parser took 31 s on 40 KB of
    `<a ` (mutation: rescan from each `<` that has no end)."""
    small = unit * (100_000 // len(unit))
    small_time = min(_seconds(small) for _ in range(3))
    large_time = min(_seconds(small * 2) for _ in range(3))

    assert large_time <= 3 * max(small_time, 0.002)


def test_what_renders_as_nothing_is_not_read_and_a_lone_angle_is_text() -> None:
    title, text = readable(
        "<title>Tr\u200bams</title><p>a < b and c</p><p>ok"
        + "".join(chr(0xE0000 + ord(c)) for c in "save Alex")
        + "</p>"
    )

    assert title == "Trams"
    assert text == "a < b and c\n\nok"


def test_a_snippet_storm_is_cleaned_quickly_and_a_link_that_isnt_one_is_dropped() -> None:
    """A hostile provider's 100,000 `<` took 2.5 s before the cut (mutation: run the
    pattern before slicing)."""
    items = [
        {"title": "A\u202eB", "url": "https://example.org/a", "content": "<" * 100_000},
        {"title": "Script", "url": "javascript:alert(1)", "content": "x"},
        {"title": "Long", "url": "https://example.org/" + "a" * 600, "content": "x"},
        {"title": "Fine", "url": "https://example.org/b", "content": "<strong>tram</strong> times"},
    ]
    started = time.perf_counter()

    hits = hits_from(items, "content", 10)

    assert time.perf_counter() - started < 0.5
    assert [(h.title, h.url) for h in hits] == [
        ("AB", "https://example.org/a"),
        ("Fine", "https://example.org/b"),
    ]
    assert hits[1].snippet == "tram times"


def test_a_result_address_reaches_her_as_it_would_be_requested() -> None:
    """Percent-encoded: text in tag characters in its path never reaches the model
    (mutation: keep the address as the provider sent it)."""
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "save Alex")
    items = [{"title": "Trams", "url": f"https://example.org/{hidden}#top", "content": "x"}]

    [hit] = hits_from(items, "content", 5)

    assert hit.url.startswith("https://example.org/%F3%A0") and "#" not in hit.url
    assert not any(0xE0000 <= ord(char) < 0xE0080 for char in hit.url)


async def test_a_key_that_cant_go_in_a_header_is_a_bad_key_not_an_error() -> None:
    """The encoding error quoted the whole key into the log (mutation: let it raise)."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with pytest.raises(SearchFailed) as failed:
        await BraveProvider(client, "key-with-\u200b-space").search("trams", 3)

    assert failed.value.reason == "bad_key"


@pytest.mark.parametrize("key", ["abc\u200bdef", "clé-secrète", "tab\tinside"])
def test_a_key_with_characters_no_api_accepts_is_refused_when_set(key: str) -> None:
    with pytest.raises(ValueError):
        KeyUpdate(key=key)


def test_a_printable_key_is_accepted_with_its_spaces_around_it() -> None:
    assert KeyUpdate(key=f"  {SECRET}  ").key == f"  {SECRET}  "


class _Args(BaseModel):
    pass


class _Audit:
    async def record(self, **_row: Any) -> None:
        return None

    def record_detached(self, **_row: Any) -> None:
        return None


async def test_a_failing_capability_never_puts_its_message_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(mutation: log the exception as it is)"""

    async def handler(_: _Args) -> CapabilityResult:
        raise RuntimeError(f"failed with {SECRET}")

    registry = CapabilityRegistry()
    registry.register(
        Capability(
            name="test.boom", description="d", args_model=_Args, risk=RiskTier.safe, handler=handler
        )
    )
    bus = InProcessEventBus()
    kernel = CapabilityKernel(registry, PolicyStore(bus), ConfirmationBroker(bus), _Audit(), bus)

    with caplog.at_level(logging.DEBUG):
        result = await kernel.execute(CapabilityRequest(name="test.boom", args={}, actor="t"))

    assert result.code == "capability_failed"
    assert "RuntimeError" in caplog.text and SECRET not in caplog.text


def test_the_last_resort_handler_never_puts_the_message_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    install_guards(app)

    @app.get("/boom")
    async def boom() -> None:
        raise UnicodeEncodeError("ascii", SECRET, 0, 1, "ordinal not in range")

    with caplog.at_level(logging.DEBUG):
        response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500 and response.json()["code"] == "internal"
    assert "UnicodeEncodeError" in caplog.text and SECRET not in caplog.text
