"""Voice Studio service: turn uploaded or recorded clips into a registered voice.

Each clip is downmixed to mono, resampled to 24 kHz and trimmed of silence; the
clips are then joined, peak-normalized and capped at 20 s to form one reference
WAV, which ``VoiceService.register_from_clip`` registers and activates. There is no
per-voice training (zero-shot cloning). Also backs preview synthesis and deletion.
List-changing operations emit ``voices.updated``.

The clips are read from memory, as they were uploaded, and the reference is written
sealed (`clips.py`): no recording of a person's voice is written out plain.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO, Protocol

import numpy as np
import numpy.typing as npt
import soundfile as sf

from app.core.data_key import Locked
from app.core.event_bus import EventBus
from app.core.sealed import SealBroken
from app.schemas.events import Event
from app.services.voice.clips import Clips
from app.services.voice.voices import Voice, VoiceRegistry, VoiceService

logger = logging.getLogger(__name__)

_TARGET_SR = 24000
_MIN_SECONDS = 6.0  # build floor for new clips: above the model's >5 s hard limit, with margin
_MODEL_MIN_SECONDS = 5.0  # the model asserts ref > 5 s; reject at/under this before it crashes
_MAX_SECONDS = 20.0
_SILENCE = 0.01
PREVIEW_SENTENCE = "Hi, I'm Veronica — this is how I'll sound on our calls."
_TOO_SHORT_BUILD = (
    "That clip's too short — give me about 10–15 seconds of clear speech: "
    "one speaker, no music or background noise."
)
_TOO_SHORT_REFERENCE = (
    "This voice's reference clip is too short to clone from. Re-create it with "
    "about 10–15 seconds of clear speech."
)
_UNREADABLE_REFERENCE = (
    "This voice's clip can't be opened: it's missing, or it was changed since it was "
    "made. Delete the voice and make it again from a clip."
)
# The default voice can't be deleted: it is made again by the script that made it.
_UNREADABLE_DEFAULT = (
    "Her default voice's clip can't be opened: it's missing, or it was changed since it "
    "was made. Make it again with uv run python -m scripts.seed_default_voice in the "
    "backend folder."
)
_STUCK = (
    "Her voices file couldn't be read or moved aside, so no voice can be saved or "
    "removed until it is. The notice at the top of the window says what to do."
)
_LOCKED = (
    "Her voices are locked on this computer, so none can be made, played or chosen "
    "until they open. The notice at the top of the window says what to do."
)


class _Synthesizer(Protocol):
    def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]: ...

    async def forget_voice(self, voice_id: str) -> None: ...


class VoiceStudioError(RuntimeError):
    """A user-facing studio failure: `code` for the app, the message in plain words."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class VoiceStudio:
    """Create / preview / delete voices on top of the registry + TTS."""

    def __init__(
        self,
        registry: VoiceRegistry,
        tts: _Synthesizer,
        voice_service: VoiceService,
        bus: EventBus,
        default_voice_id: str,
        *,
        clips: Clips,
    ) -> None:
        self._registry = registry
        self._tts = tts
        self._voices = voice_service
        self._bus = bus
        self._default = default_voice_id
        self._clips = clips

    def list_voices(self) -> list[Voice]:
        return self._registry.list()

    async def create_from_clips(
        self, name: str, uploads: list[bytes], *, source: str = "upload"
    ) -> Voice:
        """A voice made from `uploads`, each clip's file as it was uploaded or recorded."""
        if not uploads:
            raise VoiceStudioError("No audio was provided.", "no_audio")
        self._writable()
        if self._clips.locked is not None:  # a new clip couldn't be sealed
            raise VoiceStudioError(_LOCKED, "voices_locked")
        # Decode/resample/normalize is CPU + disk I/O — keep it off the event loop
        # so a clone never stalls an in-flight call.
        voice_id, ref_path = await asyncio.to_thread(self._build_reference, uploads, name)
        try:
            voice = await self._voices.register_from_clip(
                voice_id, name, str(ref_path), source=source
            )
        except Exception:
            self._cleanup_files(voice_id)  # the clip of a voice that was never saved
            raise
        await self._emit_updated()
        return voice

    async def preview(self, voice_id: str, sentence: str | None = None) -> bytes:
        if not self._registry.has(voice_id):
            raise VoiceStudioError("That voice no longer exists.", "voice_missing")
        await self.ensure_reference_ok(voice_id)  # a short one must not reach (and crash) the model
        pcm = bytearray()
        async for chunk in self._tts.synthesize_stream(sentence or PREVIEW_SENTENCE, voice_id):
            pcm.extend(chunk)
        return _pcm16_to_wav(bytes(pcm))

    async def delete(self, voice_id: str) -> str:
        """Remove a voice, falling back to the default voice if it was active.

        Returns the fallback voice_id, or '' if the active voice didn't change.
        """
        if not self._registry.has(voice_id):
            raise VoiceStudioError("That voice no longer exists.", "voice_missing")
        if voice_id == self._default:
            raise VoiceStudioError("The default voice can't be deleted.", "default_voice")
        self._writable()
        fell_back = ""
        if voice_id == self._voices.active_voice_id:
            await self._voices.set_active(self._default)
            fell_back = self._default
        self._registry.remove(voice_id)
        await self._tts.forget_voice(voice_id)  # a new voice of the same name reuses the id
        self._cleanup_files(voice_id)
        await self._emit_updated()
        return fell_back

    async def ensure_reference_ok(self, voice_id: str) -> None:
        """Raise a studio error if the voice's reference clip can't be cloned from: too
        short, locked, or unreadable.

        The model asserts that a reference is longer than 5 s, and its raw
        `AssertionError` must not reach the user. New clips are floored at 6 s on build;
        this catches an older or short reference before preview or activation reaches
        `prepare_conditionals`. A locked clip would be spoken in the model's own voice,
        not this one. A missing voice is left to the caller's own check.
        """
        if not self._registry.has(voice_id):
            return
        reference = self._registry.get(voice_id).reference_wav_path
        try:
            wav = await asyncio.to_thread(self._clips.read, reference)
        except Locked as locked:
            raise VoiceStudioError(_LOCKED, "voices_locked") from locked
        except (OSError, SealBroken) as unreadable:
            # Missing, unreadable, or changed on disk (renamed into another voice's
            # place, say): said as that, where it used to read as a clip too short.
            words = _UNREADABLE_DEFAULT if voice_id == self._default else _UNREADABLE_REFERENCE
            raise VoiceStudioError(words, "reference_unreadable") from unreadable
        if reference_seconds(wav) <= _MODEL_MIN_SECONDS:
            raise VoiceStudioError(_TOO_SHORT_REFERENCE, "reference_too_short")

    # -- internals --

    def _writable(self) -> None:
        if self._registry.stuck:
            raise VoiceStudioError(_STUCK, "voices_file_stuck")

    def _claim(self, name: str) -> tuple[str, BinaryIO]:
        """A free voice id, and its clip file made for writing. Free on disk as well as
        in the list: after a damaged voices.json is set aside the clips it named are
        still here, and a new voice of the same name wrote over one."""
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "voice"
        voice_id = base
        while True:
            if not (
                self._registry.has(voice_id) or self._registry.embedding_path_for(voice_id).exists()
            ):
                try:
                    return voice_id, (self._registry.directory / f"{voice_id}.wav").open("xb")
                except FileExistsError:
                    pass
            voice_id = f"{base}-{uuid.uuid4().hex[:6]}"

    def _build_reference(self, uploads: list[bytes], name: str) -> tuple[str, Path]:
        chunks: list[npt.NDArray[np.float32]] = []
        for upload in uploads:
            try:
                audio, sr = sf.read(io.BytesIO(upload), dtype="float32", always_2d=False)
            except Exception as exc:  # unreadable / corrupt upload
                raise VoiceStudioError(
                    "That file isn't an audio clip I can read.", "unreadable_clip"
                ) from exc
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if sr != _TARGET_SR:
                audio = _resample(audio, sr, _TARGET_SR)
            chunks.append(_trim_silence(audio))
        combined = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if combined.size < int(_MIN_SECONDS * _TARGET_SR):
            raise VoiceStudioError(_TOO_SHORT_BUILD, "clip_too_short")
        combined = _normalize(combined)[: int(_MAX_SECONDS * _TARGET_SR)]
        wav = io.BytesIO()
        sf.write(wav, combined, _TARGET_SR, format="WAV", subtype="PCM_16")
        voice_id, handle = self._claim(name)
        out = self._registry.directory / f"{voice_id}.wav"
        try:
            with handle:
                self._clips.write(handle, out, wav.getvalue())
        except Exception:
            out.unlink(missing_ok=True)
            raise
        return voice_id, out

    def _cleanup_files(self, voice_id: str) -> None:
        for path in (
            self._registry.directory / f"{voice_id}.wav",
            self._registry.embedding_path_for(voice_id),
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not remove %s: %r", path, exc)

    async def _emit_updated(self) -> None:
        await self._bus.publish(Event(type="voices.updated", source="voice_studio", payload={}))


def reference_seconds(wav: bytes) -> float:
    """Duration of a reference WAV in seconds (0.0 if unreadable — treated as too short)."""
    try:
        return float(sf.info(io.BytesIO(wav)).duration)
    except Exception:
        return 0.0


def _resample(audio: npt.NDArray[np.float32], sr: int, target_sr: int) -> npt.NDArray[np.float32]:
    import torch
    import torchaudio.functional as taf  # type: ignore[import-untyped]

    tensor = torch.from_numpy(audio).unsqueeze(0)
    out = taf.resample(tensor, sr, target_sr)
    result: npt.NDArray[np.float32] = out.squeeze(0).numpy().astype(np.float32)
    return result


def _trim_silence(audio: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    if audio.size == 0:
        return audio
    loud = np.abs(audio) > _SILENCE
    if not loud.any():
        return audio
    first = int(np.argmax(loud))
    last = int(len(loud) - np.argmax(loud[::-1]))
    return audio[first:last]


def _normalize(audio: npt.NDArray[np.float32], peak: float = 0.95) -> npt.NDArray[np.float32]:
    m = float(np.max(np.abs(audio))) if audio.size else 0.0
    return (audio * (peak / m)).astype(np.float32) if m > 0.0 else audio


def _pcm16_to_wav(pcm: bytes, sample_rate: int = _TARGET_SR) -> bytes:
    samples = np.frombuffer(pcm, dtype="<i2")
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()
