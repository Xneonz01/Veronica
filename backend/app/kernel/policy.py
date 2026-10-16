"""Per-capability permission policy: auto-allow, confirm, or deny.

Defaults derive from the risk tier, except for a guarded capability declared
`auto_approve` (one its own notification can undo), and the user can retune any
capability live. One rule is not user-overridable: a `dangerous` capability, or one
declared `always_ask` (a tool server's), can never be set to auto-allow, so a human
always sees it.

Changes are persisted to `policy.json` in her data folder. Held only in memory, a user who switched
`files.write` to deny would silently get confirm back on the next restart.
"""

from __future__ import annotations

import json
import logging
import os
from enum import StrEnum
from pathlib import Path

from app.core.event_bus import EventBus
from app.kernel.capability import RiskTier
from app.kernel.registry import Capability
from app.schemas.events import Event

logger = logging.getLogger(__name__)


class PolicyMode(StrEnum):
    auto_allow = "auto_allow"
    confirm = "confirm"
    deny = "deny"


_DEFAULTS: dict[RiskTier, PolicyMode] = {
    RiskTier.safe: PolicyMode.auto_allow,
    RiskTier.guarded: PolicyMode.confirm,
    RiskTier.dangerous: PolicyMode.confirm,
}


def _default(capability: Capability) -> PolicyMode:
    if capability.auto_approve and capability.risk is RiskTier.guarded:
        return PolicyMode.auto_allow
    return _DEFAULTS[capability.risk]


def _always_asks(capability: Capability) -> bool:
    return capability.risk is RiskTier.dangerous or capability.always_ask


class PolicyViolation(ValueError):
    """Raised when a requested policy change is not permitted."""


class PolicyStore:
    """Live-reactive capability -> mode map, seeded from config, tuned at runtime."""

    def __init__(
        self,
        bus: EventBus,
        overrides: dict[str, str] | None = None,
        *,
        path: Path | None = None,
    ) -> None:
        self._bus = bus
        self._path = path
        self._overrides: dict[str, PolicyMode] = {}
        self._apply(overrides or {})

    def _apply(self, raw: dict[str, str]) -> None:
        for name, mode in raw.items():
            try:
                self._overrides[name] = PolicyMode(mode)
            except ValueError:
                logger.warning("ignoring unknown policy mode %r for %s", mode, name)

    def load(self) -> None:
        """Layer saved choices over the config defaults; a bad file is ignored."""
        if self._path is None:
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._apply({str(k): str(v) for k, v in data.items()})

    def mode_for(self, capability: Capability) -> PolicyMode:
        mode = self._overrides.get(capability.name, _default(capability))
        # Defence in depth: even a config file cannot auto-allow a dangerous action.
        if _always_asks(capability) and mode is PolicyMode.auto_allow:
            return PolicyMode.confirm
        return mode

    async def set_mode(self, capability: Capability, mode: PolicyMode) -> None:
        if _always_asks(capability) and mode is PolicyMode.auto_allow:
            raise PolicyViolation("That action always needs confirmation.")
        self._overrides[capability.name] = mode
        self._save()
        await self._bus.publish(
            Event(
                type="policy.changed",
                source="kernel",
                payload={"capability": capability.name, "mode": mode.value},
            )
        )

    def _save(self) -> None:
        """Atomic write. A failure must not revert the live decision the user made."""
        if self._path is None:
            return
        payload = {name: mode.value for name, mode in self._overrides.items()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, self._path)
        except OSError:
            # Keep the in-memory change: reverting it would flip the switch back for a
            # reason the user cannot see or fix.
            logger.exception("could not persist capability policy; it applies until restart")
