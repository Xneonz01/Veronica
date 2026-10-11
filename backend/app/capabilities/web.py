"""Searching the web and reading a page (ADR 0003).

Both are reads, `safe`: nothing on this computer changes. What comes back is untrusted
text, fenced like any tool result (`services/tools/render.py`) and held apart as `web`
by the kernel, which makes any action in the same turn ask first. They are offered only
while web search is on in Settings, and refuse if called while it is off.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.adapters.web.fetch import FetchRefused
from app.adapters.web.search import Reason, SearchFailed, SearchHit
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import Capability, CapabilityRegistry
from app.services.connected.runtime import ConnectedRuntime

# Above the fetcher's own timeout: it may follow three redirects.
_READ_TIMEOUT_S = 40.0
_SEARCH_TIMEOUT_S = 20.0
_OFF = CapabilityResult(
    ok=False, code=Code.network_disabled, detail="Web search is switched off in Settings."
)
_WHY: dict[Reason, str] = {
    "unreachable": "I couldn't reach the search service.",
    "refused": "The search service turned that down.",
    "json_off": "The SearXNG server doesn't answer in JSON: add json to its search formats.",
    "bad_key": "The search service didn't accept the key in Settings.",
    "rate_limited": "The search service says too many searches; try again in a moment.",
    "bad_answer": "The search service gave an answer I couldn't read.",
}


class SearchArgs(BaseModel):
    model_config = {"extra": "forbid"}
    query: str = Field(min_length=1, max_length=300, description="What to search the web for.")


class ReadArgs(BaseModel):
    model_config = {"extra": "forbid"}
    url: str = Field(min_length=1, max_length=2000, description="The address of the page.")


def register_web(registry: CapabilityRegistry, web: ConnectedRuntime, results: int) -> None:
    async def search(args: SearchArgs) -> CapabilityResult:
        if not web.store.web_on():
            return _OFF
        provider = web.provider()
        if provider is None:
            detail = "Web search needs a Brave key, set in Settings."
            return CapabilityResult(ok=False, code=Code.search_unavailable, detail=detail)
        try:
            hits = await provider.search(args.query, results)
        except SearchFailed as failed:
            detail = _WHY[failed.reason]
            return CapabilityResult(ok=False, code=Code.search_unavailable, detail=detail)
        shown = f"Searched the web: {len(hits)} result{'' if len(hits) == 1 else 's'}."
        return CapabilityResult(
            ok=True, detail=shown, data={"content": _listing(hits) or "No results."}
        )

    async def read(args: ReadArgs) -> CapabilityResult:
        if not web.store.web_on():
            return _OFF
        try:
            page = await web.fetcher.fetch(args.url.strip())
        except FetchRefused as refused:
            return CapabilityResult(ok=False, code=Code(refused.code), detail=refused.detail)
        heading = "\n".join(part for part in (page.title, page.url) if part)
        body = page.text or "(The page has no text I can read.)"
        site = urlsplit(page.url).hostname or "the page"
        return CapabilityResult(
            ok=True, detail=f"Read a page on {site}.", data={"content": f"{heading}\n\n{body}"}
        )

    registry.register(
        Capability(
            name="web.search",
            description="Search the web, for current events or facts you don't know.",
            args_model=SearchArgs,
            risk=RiskTier.safe,
            handler=search,
            timeout_s=_SEARCH_TIMEOUT_S,
            voice_exposed=True,
            summarize=lambda a: f"search the web for {a.query[:80]}",
            available=web.store.web_on,
        )
    )
    registry.register(
        Capability(
            name="web.read",
            description="Read one web page, by its address, as text.",
            args_model=ReadArgs,
            risk=RiskTier.safe,
            handler=read,
            timeout_s=_READ_TIMEOUT_S,
            summarize=lambda a: f"read {a.url[:100]}",
            available=web.store.web_on,
        )
    )


def _listing(hits: list[SearchHit]) -> str:
    return "\n\n".join(
        "\n".join(part for part in (f"[{n}] {hit.title}", hit.url, hit.snippet) if part)
        for n, hit in enumerate(hits, 1)
    )
