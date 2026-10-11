"""The voice settings: recognition, synthesis, voice detection, voices and the call.

Sections of `config.Settings` (`asr`, `tts`, `vad`, `voice`, `realtime`), kept in their
own file like `config_llm.py`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.paths import Place


class AsrSettings(BaseModel):
    """Streaming speech-to-text (faster-whisper) settings."""

    model_size: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str | None = None
    partial_beam_size: int = 1
    final_beam_size: int = 5
    decode_interval_ms: int = 400


class TtsSettings(BaseModel):
    """Streaming text-to-speech (Chatterbox) settings."""

    device: str = "cuda"
    # Turbo's documented default generation parameters, kept here so they are pinned
    # and visible. Not a quality dial: don't change them without a listening test.
    # Turbo ignores cfg_weight/exaggeration/min_p, so they are omitted.
    temperature: float = 0.8
    repetition_penalty: float = 1.2
    top_p: float = 0.95
    top_k: int = 1000


class VadSettings(BaseModel):
    """Voice-activity detection (Silero) and end-of-turn detection (smart-turn)."""

    # The microphone path and Silero both run at 16 kHz; any other rate made every
    # window fail, silently, and the call never heard speech.
    sample_rate: Literal[16000] = 16000
    threshold: float = Field(default=0.5, gt=0, lt=1)  # Silero: is this window speech?
    min_speech_ms: int = Field(default=250, ge=0)
    # The end of a turn is a window, not a fixed wait. At `end_silence_min_ms` of silence
    # smart-turn is asked whether the speaker has finished, and asked again every
    # `recheck_ms`; at `end_silence_max_ms` the turn ends whatever it says. The fixed
    # 400 ms this replaces was paid in full on every turn (416 ms: 13 windows of 32 ms).
    # A floor at the ceiling, or less than a 32 ms window below it, is a fixed wait:
    # the ceiling comes before the first check, and the model is never asked.
    end_silence_min_ms: int = Field(default=200, ge=0)
    end_silence_max_ms: int = Field(default=800, gt=0)
    recheck_ms: int = Field(default=100, gt=0)
    # smart-turn's probability that the turn is complete, above which the turn ends.
    # 0.6: on the call probe's questions it ended as many finished questions early as
    # 0.5 (14 of 16) and wrongly ended fewer half-sentences (8 of 48, against 10).
    turn_complete_threshold: float = Field(default=0.6, gt=0, lt=1)

    @model_validator(mode="after")
    def _silence_window_is_a_window(self) -> VadSettings:
        # A floor above the ceiling is refused rather than read as a fixed wait: it
        # can only be a mistake, and every turn would wait out the ceiling unexplained.
        if self.end_silence_min_ms > self.end_silence_max_ms:
            raise ValueError(
                f"vad.end_silence_min_ms ({self.end_silence_min_ms}) is above "
                f"vad.end_silence_max_ms ({self.end_silence_max_ms})"
            )
        return self


class VoiceSettings(BaseModel):
    """Voice registry: where voices persist, and the voice she starts in until another
    is chosen (the one chosen is kept beside the voices, `active_voice.py`, and wins
    while she still has it)."""

    voices_dir: Place = "data/voices"  # in `Settings`, in the data folder unless set
    active_voice_id: str = "veronica-default"


class RealtimeSettings(BaseModel):
    """Realtime call session: history, reply chunking, partial throttling, metrics.

    The persona is not configured here: voice and text share the editable one in
    `services/persona/`.
    """

    history_turns: int = Field(default=8, ge=0)
    # The most one spoken reply may say, in characters: about 51 seconds of speech at
    # the measured rate (`greeting.py`), and a little over twice the longest of the 128
    # replies the call probe has recorded (415). Enforced where the model's text is read,
    # ending after a sentence (`realtime/reply_cap.py`). 0 is no ceiling.
    reply_max_chars: int = Field(default=900, ge=0)
    # Above zero: at zero the chunker's cut found no room and looped for ever.
    chunk_max_chars: int = Field(default=220, gt=0)
    clause_chunking: bool = True  # flush short opening clauses for faster first audio
    min_clause_chars: int = Field(default=16, ge=0)
    partial_throttle_ms: int = Field(default=200, ge=0)
    # In `Settings`, in the data folder unless set (`config.STORES`).
    metrics_path: Place = "data/metrics/voice_turns.jsonl"
    # How long the speech models stay loaded after the last call ends, so a call
    # started straight back doesn't wait for them to load again (`voice/calls.py`).
    release_models_after_s: float = Field(default=60.0, ge=0)
