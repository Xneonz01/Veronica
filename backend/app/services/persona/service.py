"""One personality, shared by voice and text, editable while the app is running.

Stored as `persona.json` in her data folder rather than in the DB: it is one string the
user should be able to read and hand-edit, like `voices.json`. It isn't a Tier-1
profile memory, because those are facts about the user that get injected into prompts
and shown on the Memory page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from app.core.event_bus import EventBus
from app.kernel.errors import Code
from app.schemas.events import Event
from app.services.memory.sensitive import is_sensitive
from app.services.persona.defaults import (
    DEFAULT_PERSONALITY,
    MAX_PERSONALITY_CHARS,
    build_prompt,
    sanitize,
)

logger = logging.getLogger(__name__)


class Surface(StrEnum):
    voice = "voice"
    text = "text"


class PersonaRejected(ValueError):
    """The requested personality was refused; `code` maps to on-brand copy."""

    def __init__(self, code: Code, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PersonaService:
    """Holds the live personality and composes each surface's system prompt."""

    def __init__(self, path: Path, bus: EventBus) -> None:
        self._path = path
        self._bus = bus
        self._personality = DEFAULT_PERSONALITY
        # Whether chat's prompt with a personality in it leaves room for a message. Set
        # once chat's tools are known (`startup.hold_persona_to_window`); until then,
        # and without memory, which chat needs, only the length is checked.
        self._fits_window: Callable[[str], bool] | None = None

    @property
    def personality(self) -> str:
        return self._personality

    def check_window_with(self, fits: Callable[[str], bool]) -> None:
        """From now on, refuse a personality whose chat prompt `fits` turns down."""
        self._fits_window = fits

    def load(self) -> None:
        """Read the stored personality; a missing or unreadable file uses the default."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            stored = data.get("personality")
        except (OSError, ValueError):
            return
        if isinstance(stored, str) and stored.strip():
            self._personality = sanitize(stored)

    def system_prompt(self, surface: Surface, *, tools_enabled: bool = False) -> str:
        return build_prompt(
            self._personality, spoken=surface is Surface.voice, tools_enabled=tools_enabled
        )

    def provider(self, surface: Surface, *, tools_enabled: bool = False) -> Callable[[], str]:
        """A zero-arg callable returning the current prompt.

        Sessions hold this instead of a string, so an edit applies from the next turn
        of a call or chat already in progress.
        """

        def current() -> str:
            return self.system_prompt(surface, tools_enabled=tools_enabled)

        return current

    async def set_personality(self, personality: str) -> str:
        cleaned = sanitize(personality)
        if not cleaned:
            cleaned = DEFAULT_PERSONALITY
        if len(cleaned) > MAX_PERSONALITY_CHARS:
            raise PersonaRejected(
                Code.persona_too_long,
                f"That's longer than {MAX_PERSONALITY_CHARS} characters.",
            )
        # Within the length, a text of emoji or of a rare script can still fill the
        # window: saved, it would send every chat message back.
        chat_prompt = build_prompt(cleaned, spoken=False, tools_enabled=True)
        if self._fits_window is not None and not self._fits_window(chat_prompt):
            raise PersonaRejected(
                Code.persona_fills_window,
                "That fills my context window, with no room left for a message, so I "
                "didn't save it. Shorten it, or raise llm.num_ctx in my configuration "
                "and restart my backend.",
            )
        if is_sensitive(cleaned):
            raise PersonaRejected(
                Code.sensitive_persona,
                "That looks like it contains a secret, so I didn't save it.",
            )
        # Persist first. Assigning before the write would mean a failed save (disk full,
        # file locked) reports an error while the personality has already changed.
        await asyncio.to_thread(self._write, cleaned)
        self._personality = cleaned
        await self._bus.publish(
            Event(type="persona.changed", source="persona", payload={"personality": cleaned})
        )
        return cleaned

    async def reset(self) -> str:
        return await self.set_personality(DEFAULT_PERSONALITY)

    def _write(self, personality: str) -> None:
        """Atomic write, so an interrupted save never truncates the stored persona."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps({"personality": personality}, indent=2), encoding="utf-8")
        os.replace(temp, self._path)
