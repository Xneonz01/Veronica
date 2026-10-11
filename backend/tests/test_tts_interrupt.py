"""Stopping a sentence mid-synthesis on the real model. `slow`: needs the GPU.

Uses Chatterbox Turbo's built-in voice and the weights already in the local Hugging Face
cache; nothing is downloaded, and with no cached weights the tests are skipped. What is
checked is what the fakes in `test_tts.py` can't show: that the hook on the real
model's speech-token embedding stops `generate` early, and that the model speaks a
whole sentence straight after.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from app.adapters.tts.chatterbox_stream import ChatterboxStreamTTS, chatterbox_loader
from app.core.config import TtsSettings
from app.core.data_key import Locked
from app.services.voice.voices import Voice, VoiceRegistry

pytestmark = pytest.mark.slow

LONG = (
    "This is a long sentence about the weather, the garden, the week ahead and the "
    "plans we made for it, which she will not be allowed to finish saying."
)
SHORT = "And this one she says instead."
_BUILTIN = "builtin"


class _Manager:
    def __init__(self, model: Any) -> None:
        self._model = model

    async def get(self, _model_id: str) -> Any:
        return self._model


@pytest.fixture(scope="module")
def turbo() -> Any:
    """Chatterbox Turbo as the app loads it: from the local cache, with the stop hook."""
    import torch  # noqa: F401  (as in the app: torch before chatterbox)

    try:
        return chatterbox_loader(TtsSettings())()
    except FileNotFoundError:  # the hub's `LocalEntryNotFoundError` is one too
        pytest.skip("Chatterbox Turbo isn't in the local Hugging Face cache")


def _locked(_path: str) -> bytes:
    raise Locked("another_account")


def _adapter(model: Any) -> ChatterboxStreamTTS:
    tts = ChatterboxStreamTTS(_Manager(model), TtsSettings(), registry=None, read_clip=_locked)  # type: ignore[arg-type]
    tts._conds_cache[_BUILTIN] = model.conds
    return tts


async def _pcm(tts: ChatterboxStreamTTS, text: str) -> bytes:
    return b"".join([chunk async for chunk in tts.synthesize_stream(text, _BUILTIN)])


async def test_an_abandoned_sentence_stops_and_the_next_is_whole(turbo: Any) -> None:
    tts = _adapter(turbo)
    await _pcm(tts, SHORT)  # the first generate warms the model up

    started = time.perf_counter()
    whole = await _pcm(tts, LONG)
    full_seconds = time.perf_counter() - started

    speaking = asyncio.create_task(_pcm(tts, LONG))
    await asyncio.sleep(full_seconds / 4)  # well inside the sampling loop
    cancelled_at = time.perf_counter()
    speaking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await speaking
    stopped_in = time.perf_counter() - cancelled_at

    after = await _pcm(tts, SHORT)
    assert whole and after
    assert stopped_in < full_seconds / 4, f"stopped in {stopped_in:.3f} s of {full_seconds:.3f}"
    # 24 kHz PCM16: the sentence after the stop is more than a second of speech.
    assert len(after) > 24_000 * 2


async def test_a_locked_voice_is_spoken_in_the_voice_the_model_loads_with(
    turbo: Any, tmp_path: Path
) -> None:
    """`builtin_voice` loads, from the model's folder, the voice `from_local` gives it."""
    import torch

    loaded_with = turbo.conds
    registry = VoiceRegistry(tmp_path)
    registry.add(Voice(voice_id="rowan", name="Rowan", reference_wav_path="rowan.wav"))
    tts = ChatterboxStreamTTS(_Manager(turbo), TtsSettings(), registry, read_clip=_locked)
    try:
        pcm = b"".join([chunk async for chunk in tts.synthesize_stream(SHORT, "rowan")])
        spoken_with = turbo.conds
    finally:
        turbo.conds = loaded_with  # the module's model, as the other tests expect it

    assert len(pcm) > 24_000 * 2 and spoken_with is not loaded_with
    assert torch.equal(spoken_with.t3.speaker_emb, loaded_with.t3.speaker_emb)
    assert torch.equal(spoken_with.gen["embedding"], loaded_with.gen["embedding"])
