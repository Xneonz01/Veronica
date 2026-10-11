"""The web's settings (ADR 0003): read and change them, set the Brave key, and try the
search provider. Every change is live and announced (`connected.changed`).

The key goes in and never comes out: `GET` says only whether one is set.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.adapters.web.search import SearchFailed
from app.api.refusals import Refusal
from app.services.connected.runtime import ConnectedRuntime
from app.services.connected.store import CannotKeepKey, ConnectedSettings

router = APIRouter(prefix="/connected", tags=["connected"])


class KeyUpdate(BaseModel):
    key: str | None = Field(default=None, max_length=200)


def _web(request: Request) -> ConnectedRuntime:
    runtime = getattr(request.app.state, "connected", None)
    if runtime is None:  # started without her memory (`app/stores.py`)
        raise Refusal(503, "connected_unavailable", "The web settings aren't available right now.")
    web: ConnectedRuntime = runtime
    return web


@router.get("")
async def read_settings(request: Request) -> dict[str, Any]:
    return _web(request).store.view()


@router.put("")
async def change_settings(settings: ConnectedSettings, request: Request) -> dict[str, Any]:
    web = _web(request)
    await web.store.update(settings)
    return web.store.view()


@router.put("/key")
async def set_key(update: KeyUpdate, request: Request) -> dict[str, Any]:
    web = _web(request)
    key = (update.key or "").strip() or None
    try:
        await web.store.set_key(key)
    except CannotKeepKey:
        raise Refusal(
            409, "key_not_kept", "A key can't be kept safely on this system, so I didn't save it."
        ) from None
    return web.store.view()


@router.post("/test")
async def test_search(request: Request) -> dict[str, Any]:
    """One search through the provider in Settings, whether or not web search is on, so
    it can be checked before it is switched on. Says whether it worked, never the
    provider's own words."""
    provider = _web(request).provider()
    if provider is None:
        return {"ok": False, "reason": "no_key"}
    try:
        hits = await provider.search("weather", 1)
    except SearchFailed as failed:
        return {"ok": False, "reason": failed.reason}
    return {"ok": True, "results": len(hits)}
