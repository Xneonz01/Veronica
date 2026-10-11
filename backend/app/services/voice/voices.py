"""Voice registry and runtime active-voice switching.

A voice is data: ``voice_id -> { name, reference_wav_path, prompt_text,
embedding_path }`` persisted as JSON. Adding or removing a voice never touches
code. Switching the active voice swaps the cached speaker conditioning into the
already-loaded TTS model (no reload) and broadcasts ``voice.changed`` so the UI
and session update live. The Voice Studio (``voice_studio.py``) creates voices
through ``register_from_clip``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from app.core.event_bus import EventBus
from app.paths import anchored
from app.schemas.events import Event

if TYPE_CHECKING:
    from app.services.voice.active_voice import KeptVoice

logger = logging.getLogger(__name__)


class VoiceMissing(KeyError):
    """No voice by that id: removed, or never made. Its own kind, so a route can say
    so without mistaking any other missing key for it."""


class Voice(BaseModel):
    """A registered voice: a reference clip plus its cached conditioning path."""

    voice_id: str
    name: str
    reference_wav_path: str
    prompt_text: str = ""
    embedding_path: str | None = None
    source: str = "upload"  # upload | record | seed
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


_FILE_FIELDS = ("reference_wav_path", "embedding_path")


def _files(voice: Voice) -> list[tuple[str, str]]:
    """The voice's file paths that are set, by field."""
    return [(field, path) for field in _FILE_FIELDS if (path := getattr(voice, field))]


class _VoiceActivatable(Protocol):
    """The slice of the TTS adapter the service needs (avoids a circular import)."""

    async def activate_voice(self, voice_id: str, *, load: bool = True) -> None: ...


class VoiceRegistry:
    """Persistent voice catalog backed by a single JSON file.

    A voice's files in this folder are saved by name, so the folder can move with the
    data folder; in memory every path is a full one.
    """

    def __init__(self, voices_dir: str | Path) -> None:
        self._dir = anchored(voices_dir)
        self._file = self._dir / "voices.json"
        self._voices: dict[str, Voice] = {}
        # voices.json couldn't be read at start (`_load`), and was set aside; `stuck`
        # when it couldn't be moved either, or the folder can't be made: nothing is
        # written then (`_save`), and she starts without voices rather than not at all.
        self.damaged = False
        self.stuck = False
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # a file where the folder should be, a drive gone
            self.damaged = self.stuck = True
            logger.warning("the voices folder can't be used (%s)", type(exc).__name__)
        # However a path to it is spelled ("..", a junction), for telling what is in it.
        self._real_dir = self._dir.resolve()
        if not self.stuck:
            self._load()

    def _load(self) -> None:
        try:
            # utf-8-sig: Notepad and Windows PowerShell save a mark before the text.
            raw = json.loads(self._file.read_text(encoding="utf-8-sig"))
            voices = [self._found(Voice(**entry)) for entry in raw]
        except FileNotFoundError:
            return  # no voices saved yet
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            # Damaged, closed to her, a folder in its place, or edited into a shape it
            # can't have (nested too deep to parse, too): she starts without these
            # voices, and the start says so.
            self.damaged = True
            self._set_aside(type(exc).__name__)
            return
        self._voices = {voice.voice_id: voice for voice in voices}

    def _set_aside(self, why: str) -> None:
        """Out of the way of the next save, kept for whoever can mend it."""
        stamp = f"voices.json.damaged-{datetime.now():%Y%m%d-%H%M%S}"
        aside, number = self._file.with_name(stamp), 1
        while aside.exists():  # two damaged starts in one second keep both
            number += 1
            aside = self._file.with_name(f"{stamp}-{number}")
        try:
            os.replace(self._file, aside)
        except OSError:
            self.stuck = True
        logger.warning("voices.json couldn't be read (%s); starting without its voices", why)

    def _save(self, voices: dict[str, Voice]) -> None:
        """Write `voices` whole, beside the file and then over it, so a save cut short
        leaves the last one rather than half of this."""
        if self.stuck:
            raise OSError("voices.json couldn't be read or moved, so nothing is written")
        data = [self._kept(voice).model_dump(mode="json") for voice in voices.values()]
        written = self._file.with_name("voices.json.saving")
        written.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(written, self._file)

    def _found(self, voice: Voice) -> Voice:
        return voice.model_copy(update={field: self._full(path) for field, path in _files(voice)})

    def _kept(self, voice: Voice) -> Voice:
        return voice.model_copy(update={field: self._name(path) for field, path in _files(voice)})

    def _full(self, stored: str) -> str:
        """A name is a file in this folder. A full path that no longer exists, as saved
        before 0.29.0 from wherever the folder was then, is looked for here by its name."""
        path = Path(stored)
        if not path.is_absolute():
            return str(self._dir / path)
        here = self._dir / path.name
        try:
            moved = not path.exists() and here.exists()
        except OSError:  # a folder closed to this account: the path stays as it was
            return stored
        return str(here) if moved else stored

    def _name(self, path: str) -> str:
        try:
            inside = Path(path).parent.resolve() == self._real_dir
        except OSError:
            inside = False
        return Path(path).name if inside else path

    def list(self) -> list[Voice]:
        return list(self._voices.values())

    def has(self, voice_id: str) -> bool:
        return voice_id in self._voices

    def get(self, voice_id: str) -> Voice:
        voice = self._voices.get(voice_id)
        if voice is None:
            raise VoiceMissing(voice_id)
        return voice

    def add(self, voice: Voice) -> None:
        # Saved before it is listed: a save that fails leaves no voice behind it.
        voices = {**self._voices, voice.voice_id: self._found(voice)}  # as a reload gives
        self._save(voices)
        self._voices = voices

    def remove(self, voice_id: str) -> None:
        voices = {key: voice for key, voice in self._voices.items() if key != voice_id}
        self._save(voices)
        self._voices = voices

    def embedding_path_for(self, voice_id: str) -> Path:
        return self._dir / f"{voice_id}.conds.pt"

    @property
    def directory(self) -> Path:
        return self._dir


class VoiceService:
    """Owns the active voice and coordinates switching + registration."""

    def __init__(
        self,
        registry: VoiceRegistry,
        tts: _VoiceActivatable,
        bus: EventBus,
        active_voice_id: str,
        transcriber: Callable[[str], Awaitable[str]] | None = None,
        needs_prompt_text: bool = False,
        kept: KeptVoice | None = None,
    ) -> None:
        self._registry = registry
        self._tts = tts
        self._bus = bus
        self._active = active_voice_id
        self._kept = kept
        # One switch at a time, whole: two at once (two clicks, or a voice made while
        # another is chosen) wrote the kept file over each other, and left it unreadable
        # or on the voice that lost.
        self._switching = asyncio.Lock()
        self._transcriber = transcriber
        self._needs_prompt_text = needs_prompt_text

    @property
    def active_voice_id(self) -> str:
        return self._active

    async def set_active(self, voice_id: str) -> None:
        """Swap to ``voice_id`` and broadcast the change.

        Never loads the voice model: after a call it is released, and choosing a voice
        then loaded it again (seconds, and 3 GB held until the next call ended). If it
        is in memory the swap is prepared now; otherwise its next load applies it.
        """
        voice = self._registry.get(voice_id)
        async with self._switching:
            await self._tts.activate_voice(voice_id, load=False)
            self._active = voice_id
            if self._kept is not None:  # for the next start too (bug 47)
                await asyncio.to_thread(self._kept.write, voice_id)
            await self._bus.publish(
                Event(
                    type="voice.changed",
                    source="voice",
                    payload={"voice_id": voice_id, "name": voice.name},
                )
            )

    async def register_from_clip(
        self, voice_id: str, name: str, reference_wav_path: str, *, source: str = "upload"
    ) -> Voice:
        """Register a voice from a prepared reference clip and activate it.

        The clip is transcribed only when the active TTS engine needs a
        ``prompt_text`` (Chatterbox clones from audio alone, so it's skipped).
        """
        prompt_text = ""
        if self._needs_prompt_text and self._transcriber is not None:
            prompt_text = await self._transcriber(reference_wav_path)
        voice = Voice(
            voice_id=voice_id,
            name=name,
            reference_wav_path=reference_wav_path,
            prompt_text=prompt_text,
            embedding_path=str(self._registry.embedding_path_for(voice_id)),
            source=source,
        )
        self._registry.add(voice)
        await self.set_active(voice_id)
        return voice
