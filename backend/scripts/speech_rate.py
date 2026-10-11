"""How fast the synthesiser speaks: the measurement behind the greeting's length cap.

    uv run --no-sync python -m scripts.speech_rate --url ws://127.0.0.1:8765

The greeting is capped in characters (`greeting.OPENER_MAX_CHARS`), because that is
what can be counted before there is any audio, and a character cap only means
something once a character's cost in seconds has been measured. Each opener below is
spoken by the server's own TTS in its active voice, through the push-to-talk harness,
and timed by the audio that comes back. Nothing is cached: every run measures.

The fit is `seconds = fixed + per_char x characters`, by least squares, over whole
openers of the kind the greeting builds. Each is spoken `--repeats` times, because
the synthesiser samples and no two readings come out the same length. Re-run it when
the synthesiser, its settings or the default voice change, and update the constants
in `greeting.py` with the result.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from websockets.exceptions import WebSocketException

from app.services.voice.realtime.greeting import FIXED_S, OPENER_SECONDS, PER_CHAR_S
from scripts import probe_audio

# Openers as the greeting builds them, from a bare hello to one carrying a long recap
# of the last call. The names and the recaps are made up.
OPENERS = (
    "Welcome back.",
    "Good morning, Rowan.",
    "Evening, Morgan — good to hear you.",
    "Good afternoon, Rowan. Back with me?",
    "Hi — I'm Veronica. I don't think we've met. What should I call you?",
    "Welcome back, Morgan. Last time, we were planning the garden.",
    "Good evening, Rowan — good to hear you. Last time, we compared two laptops for your sister.",
    "Welcome back. Last time, we went through your notes for Thursday's talk and cut the "
    "introduction down.",
    "Morning, Morgan. Back with me? Last time, we worked out a weekly budget and how much "
    "to put aside for the trip in the spring.",
    "Good afternoon, Rowan — good to hear you. Last time, we planned the move: which boxes "
    "to pack first, and who to call about the internet before the end of the month.",
)
_TIMEOUT_S = 120.0  # the first opener waits for the voice model to load


@dataclass(frozen=True)
class Reading:
    """One opener, spoken once: its length in characters and in seconds."""

    chars: int
    seconds: float


def fit(readings: list[Reading]) -> tuple[float, float]:
    """Least squares over the readings: `(fixed seconds, seconds per character)`."""
    count = len(readings)
    sum_x = sum(reading.chars for reading in readings)
    sum_y = sum(reading.seconds for reading in readings)
    sum_xx = sum(reading.chars * reading.chars for reading in readings)
    sum_xy = sum(reading.chars * reading.seconds for reading in readings)
    denominator = count * sum_xx - sum_x * sum_x
    if denominator == 0:  # fewer than two readings, or all of one length
        raise ValueError("a fit needs readings of at least two different lengths")
    per_char = (count * sum_xy - sum_x * sum_y) / denominator
    return (sum_y - per_char * sum_x) / count, per_char


def seconds_of(pcm: bytes) -> float:
    """How long the TTS's 24 kHz PCM16 plays for."""
    return len(pcm) / 2 / probe_audio.TTS_RATE


async def measure(url: str, repeats: int) -> list[Reading]:
    readings: list[Reading] = []
    for text in OPENERS:
        for _ in range(repeats):
            pcm = await probe_audio.synthesize(url, text, timeout_s=_TIMEOUT_S)
            reading = Reading(len(text), seconds_of(pcm))
            readings.append(reading)
            print(f"  {reading.chars:4d} chars  {reading.seconds:6.2f} s  {text[:56]}")
    return readings


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure how fast the synthesiser speaks.")
    parser.add_argument("--url", default="ws://127.0.0.1:8000", help="the backend's socket URL")
    parser.add_argument("--repeats", type=int, default=2, help="readings of each opener")
    options = parser.parse_args()
    if options.repeats < 1:
        parser.error("--repeats must be at least 1")
    try:
        readings = asyncio.run(measure(options.url, options.repeats))
    except (OSError, TimeoutError, WebSocketException) as exc:
        print(f"Measurement failed: {exc!r}", file=sys.stderr)
        return 1
    fixed, per_char = fit(readings)
    chars = sum(reading.chars for reading in readings)
    seconds = sum(reading.seconds for reading in readings)
    print()
    print(f"{len(readings)} readings: {chars} characters in {seconds:.1f} s")
    print(
        f"fit: {fixed:.2f} s + {per_char:.4f} s per character "
        f"({1 / per_char:.1f} characters a second once speaking)"
    )
    print(
        f"an opener of {OPENER_SECONDS:g} s holds {int((OPENER_SECONDS - fixed) / per_char)} "
        f"characters; greeting.py has {FIXED_S} s + {PER_CHAR_S} s per character"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
