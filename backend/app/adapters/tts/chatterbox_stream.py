"""Streaming Chatterbox TTS adapter with per-voice conditioning caching.

The Chatterbox model is owned by the GPU manager. Each voice's speaker
conditioning is extracted once via ``prepare_conditionals`` and cached in memory
(nothing is written to ``Voice.embedding_path``). Switching voices swaps the cached
conditioning into the loaded model without a reload, so voice changes are
near-instant. Output is 24 kHz mono PCM16, synthesized a sentence at a time so
audio starts flowing before the full reply is ready.

The model holds one voice at a time (`model.conds`), so setting the voice and
generating happen together, under one lock, for every sentence. Between two sentences
there is an `await`, and a Voice Studio preview in that gap once switched the rest of a
call's reply to the preview's voice, or ran a second `generate` beside the call's.

A voice's reference clip is sealed at rest (ADR 0001), so it reaches the model as bytes
from `read_clip`, opened in memory, never as a path. A clip that is locked (her voices'
key can't be used here, `voices_locked`) is spoken in the voice the model comes with,
so a call still starts; and so is a voice that isn't there (her default voice never made,
bug 49), which is recorded from that voice, and one whose clip can't be opened (missing,
or changed since it was sealed).
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

from app.adapters import model_files
from app.adapters.tts.interruptible import install_abandon_check, until_out
from app.adapters.tts.text import sanitize_for_tts, split_sentences
from app.core.config_voice import TtsSettings
from app.core.data_key import Locked
from app.core.gpu_manager import GpuModelManager, ModelLoader
from app.core.sealed import SealBroken
from app.services.voice.audio import float32_to_pcm16_bytes
from app.services.voice.voices import Voice, VoiceMissing, VoiceRegistry

logger = logging.getLogger(__name__)

TTS_MODEL_ID = "tts"


def chatterbox_loader(settings: TtsSettings) -> ModelLoader:
    """Loader the GPU manager calls to construct the Chatterbox model."""

    def load() -> object:
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        # `from_local`, not `from_pretrained`, which asks the hub for a newer revision.
        model = ChatterboxTurboTTS.from_local(model_files.turbo_path(), settings.device)
        install_abandon_check(model)
        return model

    return load


def builtin_voice(device: str) -> Any:
    """The voice Chatterbox Turbo comes with (`conds.pt` in its folder), as `from_local`
    loads it."""
    from chatterbox.tts_turbo import Conditionals

    path = Path(model_files.turbo_path()) / "conds.pt"
    return Conditionals.load(path, map_location="cpu").to(device)


class ChatterboxStreamTTS:
    """Per-sentence streaming TTS with a cached, swappable speaker conditioning."""

    needs_prompt_text = False  # Chatterbox clones from audio alone (no transcript)

    def __init__(
        self,
        manager: GpuModelManager,
        settings: TtsSettings,
        registry: VoiceRegistry,
        model_id: str = TTS_MODEL_ID,
        *,
        read_clip: Callable[[str], bytes],
    ) -> None:
        self._manager = manager
        self._settings = settings
        self._registry = registry
        self._model_id = model_id
        self._read_clip = read_clip  # a clip's WAV bytes, opened; `Locked` when it can't be
        self._conds_cache: dict[str, Any] = {}
        self._builtin: Any = None  # the model's own voice, loaded the first time it's needed
        self._voice_lock = asyncio.Lock()

    async def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        """Yield 24 kHz PCM16 audio, one sentence at a time, in the given voice."""
        cleaned = sanitize_for_tts(text)
        if not cleaned:
            return
        model = await self._manager.get(self._model_id)
        for sentence in split_sentences(cleaned):
            async with self._voice_lock:
                await self._ensure_voice_active(model, voice_id)
                pcm = await until_out(partial(self._synthesize_sentence, model, sentence))
            # Outside the lock: a caller that stops reading (a barge-in) must not keep
            # it until the abandoned generator is finalised.
            yield pcm

    async def activate_voice(self, voice_id: str, *, load: bool = True) -> None:
        """Pre-warm a voice's conditioning so the first turn isn't slowed by it.

        With `load=False` it does so only if the model is in memory. Otherwise there is
        nothing to warm: the next call's warm-up, or the first sentence, applies the
        voice when the model loads.
        """
        if not load and not self._manager.is_loaded(self._model_id):
            return
        model = await self._manager.get(self._model_id)
        async with self._voice_lock:
            await self._ensure_voice_active(model, voice_id)

    async def forget_voice(self, voice_id: str) -> None:
        """Drop a deleted voice's conditioning.

        A voice's id is made from its name, so a new voice given a deleted one's name
        gets its id back, and would otherwise be spoken with the deleted voice's clone.
        Under the lock: a preview still preparing the voice would put it back after.
        """
        async with self._voice_lock:
            self._conds_cache.pop(voice_id, None)

    async def _ensure_voice_active(self, model: Any, voice_id: str) -> None:
        """Point the model at `voice_id`'s conditioning. Callers hold `_voice_lock`.

        The model is asked which it holds, rather than the last one set remembered: a
        preparation cancelled halfway still leaves the model holding the voice it was
        preparing, and a model loaded again comes back with Chatterbox's own voice.

        A voice that isn't there is spoken in the model's own voice and isn't cached, so a
        voice made under its id later (her default voice, seeded) is used from its next
        sentence. Bug 49: it failed every call's warm-up, as "check that the backend is
        running". Asked by id only for the call: the Voice Studio refuses a voice that
        isn't there before it speaks.
        """
        conds = self._conds_cache.get(voice_id)
        if conds is not None and model.conds is conds:
            return
        if conds is None:
            try:
                voice = self._registry.get(voice_id)
            except VoiceMissing:
                model.conds = await until_out(partial(self._own_voice, model, "no such voice"))
                return
            # `prepare_conditionals` sets `model.conds` itself, so its thread has to be
            # covered by the lock like a sentence's.
            conds = await until_out(partial(self._prepare_conds, model, voice))
            self._conds_cache[voice_id] = conds
        model.conds = conds

    def _prepare_conds(self, model: Any, voice: Voice) -> Any:
        try:
            clip = self._read_clip(voice.reference_wav_path)
        except Locked:
            return self._own_voice(model, "her voices are locked")
        except (OSError, SealBroken) as unusable:
            # Missing, or changed since it was sealed: the call still starts. It failed
            # its warm-up as "check that the backend is running", like a voice that isn't
            # there (bug 49); the Voice Studio says what happened to the clip.
            logger.warning("a voice's clip can't be opened (%s)", type(unusable).__name__)
            return self._own_voice(model, "a voice's clip can't be opened")
        model.prepare_conditionals(io.BytesIO(clip))
        return model.conds

    def _own_voice(self, model: Any, why: str) -> Any:
        """The voice the model comes with, loaded the first time it's needed."""
        if self._builtin is None:
            logger.warning("%s, so she speaks in the model's own voice", why)
            self._builtin = builtin_voice(model.device)
        return self._builtin

    def _synthesize_sentence(self, model: Any, sentence: str) -> bytes:
        # Turbo's documented defaults, passed explicitly so they're version-pinned and
        # set in one place. cfg_weight/exaggeration/min_p are omitted: Turbo ignores them.
        wav = model.generate(
            sentence,
            temperature=self._settings.temperature,
            repetition_penalty=self._settings.repetition_penalty,
            top_p=self._settings.top_p,
            top_k=self._settings.top_k,
        )
        samples = wav.squeeze(0).detach().cpu().numpy().astype(np.float32)
        return float32_to_pcm16_bytes(samples)
