"""Streaming faster-whisper STT adapter (LocalAgreement-2 partials + final).

The Whisper model is owned by the GPU manager; this adapter requests it and
never constructs it directly. One call to ``stream_transcripts`` covers a single
utterance: the caller feeds 16 kHz PCM16 frames and closes the stream at
end-of-turn, at which point a higher-beam final decode is emitted.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator

import numpy as np
import numpy.typing as npt

from app.adapters import model_files
from app.adapters.stt import one_pass
from app.adapters.stt.local_agreement import LocalAgreement
from app.core.config_voice import AsrSettings
from app.core.gpu_manager import GpuModelManager, ModelLoader
from app.services.voice.audio import CAPTURE_SAMPLE_RATE, Transcript, pcm16_bytes_to_float32

STT_MODEL_ID = "stt"
_MIN_DECODE_SAMPLES = CAPTURE_SAMPLE_RATE // 2  # don't decode under ~0.5 s of audio


def whisper_loader(settings: AsrSettings) -> ModelLoader:
    """Build the loader the GPU manager calls to construct the Whisper model."""

    def load() -> object:
        from faster_whisper import WhisperModel

        # From the folder on disk, never the hub (`model_files`).
        return WhisperModel(
            model_files.whisper_path(settings.model_size),
            device=settings.device,
            compute_type=settings.compute_type,
        )

    return load


class FasterWhisperStreamSTT:
    """Streaming STT over faster-whisper using the LocalAgreement-2 policy."""

    def __init__(
        self,
        manager: GpuModelManager,
        settings: AsrSettings,
        model_id: str = STT_MODEL_ID,
    ) -> None:
        self._manager = manager
        self._settings = settings
        self._model_id = model_id
        self._interval_samples = CAPTURE_SAMPLE_RATE * settings.decode_interval_ms // 1000

    async def stream_transcripts(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        """Yield confirmed partials during speech and one final at end-of-turn."""
        model = await self._manager.get(self._model_id)
        policy = LocalAgreement()
        buffer = np.zeros(0, dtype=np.float32)
        pending = 0

        async for frame in audio:
            buffer = np.concatenate([buffer, pcm16_bytes_to_float32(frame)])
            pending += len(frame) // 2
            if pending >= self._interval_samples and buffer.size >= _MIN_DECODE_SAMPLES:
                pending = 0
                words = await asyncio.to_thread(
                    self._decode_words, model, buffer, self._settings.partial_beam_size
                )
                if policy.add_hypothesis(words):
                    yield Transcript(policy.confirmed_text, is_final=False)

        final = await asyncio.to_thread(self._final, model, buffer)
        yield Transcript(final.strip(), is_final=True)

    async def transcribe_stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]:
        """STTProvider contract: yield the final transcript text for the utterance."""
        async for transcript in self.stream_transcripts(audio):
            if transcript.is_final:
                yield transcript.text

    async def transcribe_clip(self, wav: bytes) -> str:
        """One-shot transcription of a whole clip (a voice's prompt_text), given opened:
        a clip is sealed at rest, and its path would be read as the sealed file.

        faster-whisper decodes + resamples it itself (via PyAV), so any
        input sample rate works.
        """
        model = await self._manager.get(self._model_id)
        text = await asyncio.to_thread(self._transcribe_clip, model, io.BytesIO(wav))
        return text.strip()

    def _transcribe_clip(self, model: object, clip: io.BytesIO) -> str:
        segments, _ = model.transcribe(  # type: ignore[attr-defined]
            clip,
            beam_size=self._settings.final_beam_size,
            language=self._settings.language,
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments)

    def _final(self, model: object, audio: npt.NDArray[np.float32]) -> str:
        """The end-of-turn decode, with the audio encoded once (`one_pass`).

        A configured language has no detection to share, so it decodes as before.
        Partials still detect on each decode: under a second of speech is too little
        to trust a detection for the rest of the utterance.
        """
        beam = self._settings.final_beam_size
        if self._settings.language is not None:
            return self._decode_text(model, audio, beam)
        features, encoded = one_pass.encode_once(model, audio)
        return one_pass.decode(model, features, encoded, one_pass.detect(model, encoded), beam)

    def _decode_text(self, model: object, audio: npt.NDArray[np.float32], beam: int) -> str:
        segments, _ = model.transcribe(  # type: ignore[attr-defined]
            audio,
            beam_size=beam,
            language=self._settings.language,
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments)

    def _decode_words(self, model: object, audio: npt.NDArray[np.float32], beam: int) -> list[str]:
        return self._decode_text(model, audio, beam).split()
