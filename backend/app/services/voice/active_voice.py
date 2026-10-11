"""The voice she was last set to speak in, kept across restarts (bug 47).

Choosing a voice used to change only the running process, so every start went back to
the default. The choice is kept in its own small file in the voices folder, so it moves
with the data folder like the voices do; a kept voice that has since been removed, or
whose entry was set aside with a damaged voices.json, gives way to the default.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from app.telemetry import current_blackbox

if TYPE_CHECKING:
    from app.services.voice.voices import VoiceRegistry

logger = logging.getLogger(__name__)

FILE_NAME = "active_voice.json"


class KeptVoice:
    """The kept choice, read once at start and written on every change."""

    def __init__(self, voices_dir: Path) -> None:
        self._file = voices_dir / FILE_NAME

    def read(self) -> str | None:
        """The voice last chosen, or None: nothing kept yet, or a file that can't be read."""
        try:
            # utf-8-sig: Notepad and Windows PowerShell save a mark before the text.
            raw = json.loads(self._file.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError) as exc:  # nested too deep to parse
            logger.warning("the chosen voice couldn't be read (%s)", type(exc).__name__)
            return None
        voice_id = raw.get("voice_id") if isinstance(raw, dict) else None
        return voice_id if isinstance(voice_id, str) and voice_id else None

    def write(self, voice_id: str) -> None:
        """Keep `voice_id`, written beside the file and then over it. A choice that can't
        be kept still applies until the backend stops, so a failure is only recorded."""
        written = self._file.with_name(f"{FILE_NAME}.saving")
        try:
            written.write_text(json.dumps({"voice_id": voice_id}), encoding="utf-8")
            os.replace(written, self._file)
        except OSError as exc:
            logger.warning("the chosen voice couldn't be kept (%s)", type(exc).__name__)
            current_blackbox().record("warn", "voice.choice_unkept", error=type(exc).__name__)


def starting_voice(kept: str | None, registry: VoiceRegistry, default: str) -> str:
    """The kept voice while it is still a voice she has, with its clip on disk; else the
    default. A clip deleted since failed every call's warm-up, and a restart, which went
    back to the default before the choice was kept, no longer mended it. Reads the disk."""
    if kept is None or not registry.has(kept):
        return default
    try:
        present = Path(registry.get(kept).reference_wav_path).is_file()
    except OSError:  # a folder closed to her: not a clip she can speak from
        present = False
    return kept if present else default
