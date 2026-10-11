"""Question audio for the call probe, in the format a microphone sends.

The questions are spoken once by the server's own TTS, through the push-to-talk
harness (`/ws/voice/harness`), and cached as WAV files: 16 kHz, mono, PCM16, the
format the browser streams. That needs no microphone and loads no model in this
process, so while a probe runs the only work on the GPU is the backend's own.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import wave
from pathlib import Path

import numpy as np
from websockets.asyncio.client import connect

MIC_RATE = 16_000
TTS_RATE = 24_000  # what the TTS adapter produces
FRAME_SAMPLES = 320  # 20 ms, as the browser's capture worklet sends it
FRAME_BYTES = FRAME_SAMPLES * 2
# A 20 ms frame whose peak stays under this (about -40 dBFS) counts as quiet.
_QUIET_PEAK = 0.01


def clip_path(folder: Path, text: str) -> Path:
    """Where the recording of `text` is cached; the name follows the text."""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return folder / f"question-{digest}.wav"


async def synthesize(base_url: str, text: str, *, timeout_s: float) -> bytes:
    """24 kHz PCM16 of `text`, spoken by the server's TTS in the active voice."""
    chunks: list[bytes] = []
    async with connect(f"{base_url}/ws/voice/harness", proxy=None, max_size=None) as socket:
        await socket.send(json.dumps({"action": "speak", "text": text}))
        try:
            async with asyncio.timeout(timeout_s):
                async for message in socket:
                    if isinstance(message, bytes):
                        chunks.append(message)
                    elif json.loads(message).get("type") == "tts_done":
                        break
        except TimeoutError as exc:
            raise TimeoutError(
                f"the server's TTS didn't speak {text!r} within {timeout_s:.0f} s"
            ) from exc
    return b"".join(chunks)


def to_mic_audio(pcm_24k: bytes) -> bytes:
    """Resample TTS output to the microphone's rate and cut its quiet tail.

    Linear interpolation is plenty for a voice detector and a speech recogniser. The
    tail is cut so that "the question ended" means its last sound, not the hush the
    TTS leaves after it; the result is a whole number of 20 ms frames.
    """
    samples = np.frombuffer(pcm_24k, dtype="<i2").astype(np.float32) / 32768
    if samples.size == 0:  # the TTS said nothing, e.g. the text was all symbols
        return b""
    count = len(samples) * MIC_RATE // TTS_RATE
    positions = np.arange(count) * (TTS_RATE / MIC_RATE)
    resampled = np.interp(positions, np.arange(len(samples)), samples)
    frames = len(resampled) // FRAME_SAMPLES
    peaks = np.abs(resampled[: frames * FRAME_SAMPLES]).reshape(frames, FRAME_SAMPLES).max(axis=1)
    loud = np.flatnonzero(peaks >= _QUIET_PEAK)
    end = (int(loud[-1]) + 1) * FRAME_SAMPLES if loud.size else 0
    return (np.clip(resampled[:end], -1.0, 1.0) * 32767).astype("<i2").tobytes()


def save(path: Path, pcm_16k: bytes) -> None:
    """Write microphone-format audio as a WAV file, creating its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(MIC_RATE)
        clip.writeframes(pcm_16k)


def load(path: Path) -> bytes:
    """A cached clip's samples; anything but 16 kHz mono PCM16 is refused."""
    with wave.open(str(path), "rb") as clip:
        shape = (clip.getnchannels(), clip.getsampwidth(), clip.getframerate())
        if shape != (1, 2, MIC_RATE):
            raise ValueError(f"{path.name} is not 16 kHz mono PCM16")
        return clip.readframes(clip.getnframes())


def to_frames(pcm_16k: bytes) -> list[bytes]:
    """Split audio into 20 ms frames; a trailing partial frame is dropped."""
    usable = len(pcm_16k) - len(pcm_16k) % FRAME_BYTES
    return [pcm_16k[start : start + FRAME_BYTES] for start in range(0, usable, FRAME_BYTES)]


async def question_frames(
    base_url: str, text: str, folder: Path, *, timeout_s: float
) -> list[bytes]:
    """The question as 20 ms frames: from the cache, or spoken and cached now."""
    path = clip_path(folder, text)
    if not path.is_file():
        audio = to_mic_audio(await synthesize(base_url, text, timeout_s=timeout_s))
        if not audio:
            raise ValueError(f"the server's TTS returned no audible speech for {text!r}")
        save(path, audio)
    return to_frames(load(path))
