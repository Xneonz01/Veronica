"""Audio types and PCM helpers for the voice pipeline.

Capture path: 16 kHz mono PCM16 (STT + VAD). Playback path: 24 kHz mono PCM16
(Chatterbox native). Audio crosses the WebSocket transport as raw little-endian
PCM16 bytes; these helpers convert to/from the float32 arrays the models want.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

CAPTURE_SAMPLE_RATE = 16000
PLAYBACK_SAMPLE_RATE = 24000
_PCM16_FULL_SCALE = 32768.0


@dataclass(frozen=True, slots=True)
class Transcript:
    """A streaming STT result: a confirmed partial, or the final utterance."""

    text: str
    is_final: bool


def pcm16_bytes_to_float32(data: bytes) -> npt.NDArray[np.float32]:
    """Decode little-endian PCM16 bytes to float32 samples in [-1, 1].

    A trailing odd byte is dropped rather than raising: this runs on every mic frame
    inside the ingest loop, and an exception here would kill that loop, leaving the
    call connected but deaf while the client still shows "listening".
    """
    if len(data) % 2:
        data = data[:-1]
    ints = np.frombuffer(data, dtype="<i2")
    return ints.astype(np.float32) / _PCM16_FULL_SCALE


def float32_to_pcm16_bytes(samples: npt.NDArray[np.float32]) -> bytes:
    """Encode float32 samples in [-1, 1] to little-endian PCM16 bytes."""
    clipped = np.clip(samples, -1.0, 1.0)
    # Asymmetric on purpose: encode with (32768 - 1) so +1.0 maps to +32767
    # (max int16, not +32768 which overflows); decode divides by 32768.
    return (clipped * (_PCM16_FULL_SCALE - 1)).astype("<i2").tobytes()
