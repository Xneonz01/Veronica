"""The final's one encoder pass against faster-whisper's own two. `slow`: needs the GPU.

The clips are the call probe's cached questions (`data/probes`, never committed), read
by path. They are synthetic speech, which is fine for this: it checks that two code
paths give the same language and text for the same input, not how well anyone is
heard. With no cached clips the test is skipped, and says so.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from app.adapters.stt import one_pass
from app.adapters.stt.faster_whisper_stream import whisper_loader
from app.core.config import AsrSettings

pytestmark = pytest.mark.slow

PROBE_AUDIO = Path(__file__).resolve().parents[2] / "data" / "probes"
CLIPS = sorted(PROBE_AUDIO.glob("*.wav"))


@pytest.fixture(scope="module")
def whisper() -> Any:
    """The model as the app loads it, from the local cache only."""
    # torch first, as in the app, where the GPU manager has imported it before a load.
    import torch  # noqa: F401

    return whisper_loader(AsrSettings())()


def _read(path: Path) -> npt.NDArray[np.float32]:
    with wave.open(str(path), "rb") as clip:
        pcm = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


@pytest.mark.skipif(not CLIPS, reason="no cached probe audio in data/probes")
@pytest.mark.parametrize("clip", CLIPS, ids=[clip.stem for clip in CLIPS])
def test_one_pass_matches_transcribe(whisper: Any, clip: Path) -> None:
    audio = _read(clip)
    beam = AsrSettings().final_beam_size
    segments, info = whisper.transcribe(
        audio, beam_size=beam, language=None, condition_on_previous_text=False
    )
    expected = "".join(segment.text for segment in segments)

    features, encoded = one_pass.encode_once(whisper, audio)
    language = one_pass.detect(whisper, encoded)
    assert language == info.language
    assert one_pass.decode(whisper, features, encoded, language, beam) == expected
