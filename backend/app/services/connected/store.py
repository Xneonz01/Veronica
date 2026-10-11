"""What the user has switched on of the web, held live (ADR 0003).

Modelled on `kernel/policy.py`'s `PolicyStore`: one JSON file in her data folder
(`connected.json`), saved atomically, and a `connected.changed` event on every change,
so the tools she is offered, her persona's line about the web and every open Settings
screen follow at once, with no restart.

The Brave key is sealed with her data key (`core/sealed.py`), as her voice clips are.
It is never returned: what anyone outside this module learns is whether one is set.
Where there is no data key to seal it with (not Windows), it isn't kept at all.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.event_bus import EventBus
from app.core.sealed import SealBroken, seal, unseal
from app.schemas.events import Event

logger = logging.getLogger(__name__)

Provider = Literal["searxng", "brave"]
_KEY_NAME = "brave_key"  # bound into the seal, so the blob can't stand in for another
_KEY_FIELD = "brave_key_sealed"


class CannotKeepKey(Exception):
    """There is no data key here to seal a key with."""


class ConnectedSettings(BaseModel):
    model_config = {"extra": "ignore"}

    web_search: bool = False
    provider: Provider = "searxng"
    searxng_url: str = Field(default="http://127.0.0.1:8888", max_length=500)
    blocked_hosts: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("searxng_url")
    @classmethod
    def _http(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value

    @field_validator("blocked_hosts")
    @classmethod
    def _hosts(cls, hosts: list[str]) -> list[str]:
        cleaned = (host.strip().lower().rstrip(".") for host in hosts)
        return sorted({host for host in cleaned if host and len(host) <= 253})


class ConnectedStore:
    def __init__(self, bus: EventBus, path: Path, data_key: bytes | None) -> None:
        self._bus = bus
        self._path = path
        self._data_key = data_key
        self._settings = ConnectedSettings()
        self._sealed: str | None = None
        self._key: str | None = None

    def load(self) -> None:
        """The saved settings; a missing or unreadable file leaves everything off."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._settings = ConnectedSettings.model_validate(raw)
        except (OSError, ValueError, ValidationError):
            return
        sealed = raw.get(_KEY_FIELD) if isinstance(raw, dict) else None
        self._sealed = sealed if isinstance(sealed, str) else None

    @property
    def settings(self) -> ConnectedSettings:
        return self._settings

    def web_on(self) -> bool:
        return self._settings.web_search

    def blocked(self) -> list[str]:
        return self._settings.blocked_hosts

    def brave_key(self) -> str | None:
        """The key, opened once; None when none is set or it can't be opened here."""
        if self._key is None and self._sealed and self._data_key is not None:
            try:
                blob = base64.b64decode(self._sealed, validate=True)
                self._key = unseal(blob, self._data_key, _KEY_NAME).decode("utf-8")
            except (ValueError, SealBroken):
                logger.warning("the saved Brave key can't be opened; set it again")
        return self._key

    def view(self) -> dict[str, Any]:
        """What a surface may show: the settings, and whether a key is set."""
        return {**self._settings.model_dump(), "brave_key_set": self.brave_key() is not None}

    async def update(self, settings: ConnectedSettings) -> None:
        self._settings = settings
        await self._changed()

    async def set_key(self, key: str | None) -> None:
        """Keep `key`, sealed, or forget the one kept. `CannotKeepKey` without a data key."""
        if key is None:
            self._sealed = self._key = None
        else:
            if self._data_key is None:
                raise CannotKeepKey
            blob = seal(key.encode("utf-8"), self._data_key, _KEY_NAME)
            self._sealed, self._key = base64.b64encode(blob).decode("ascii"), key
        await self._changed()

    async def _changed(self) -> None:
        self._save()
        await self._bus.publish(
            Event(type="connected.changed", source="connected", payload=self.view())
        )

    def _save(self) -> None:
        """Atomic write. A failure keeps the live choice: reverting it would flip a switch
        back for a reason the user can't see."""
        payload: dict[str, Any] = self._settings.model_dump()
        if self._sealed:
            payload[_KEY_FIELD] = self._sealed
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, self._path)
        except OSError:
            logger.exception("could not save the web settings; they apply until restart")
