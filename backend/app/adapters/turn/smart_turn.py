"""smart-turn: has the speaker finished, or only paused?

Silero says whether a 32 ms window holds speech. It can't tell a breath in the middle of
a sentence from the end of a question, so the endpointer used to wait a fixed 400 ms of
silence on every turn. smart-turn is a Whisper Tiny encoder with a linear head, trained
to score the last 8 s of audio for "the turn is complete". The endpointer asks it only
once a pause is long enough to matter, so it costs nothing while someone is talking.

**v3.1, not v3.2.** The repository ships both, and v3.2 can't tell the cases apart:
measured on the call probe's eight questions, it scored the finished questions 0.93 to
0.98, the same questions cut off halfway 0.96 to 0.99, and silence 0.99. With the 200 ms
pause the endpointer waits before asking, v3.1 scored the cut-off halves 0.03 to 0.12,
and five of the eight finished questions 0.79 to 0.98 (the other three 0.01 to 0.46,
which the endpointer then waits out).

The output tensor is named `logits` but is already a probability (every score above
falls in [0, 1]); a second sigmoid would squash them all towards 0.7.

A CPU model the call owns, like Silero, so it is opened here and not through the GPU
manager. It is opened once and shared by every call: the ONNX session and the feature
extractor are shared state, so one lock covers both.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
import numpy.typing as npt

from app.adapters import model_files
from app.adapters.base import TurnArbiter
from app.services.voice.audio import CAPTURE_SAMPLE_RATE

# The model's whole input is 8 s at 16 kHz. Longer audio keeps the end: the question is
# about the pause that just happened, not how the sentence began.
WINDOW_SAMPLES = 8 * CAPTURE_SAMPLE_RATE
# Measured per call: 37.6 ms on one intra-op thread, 21.2 on two, 13.1 on four. Four
# keeps an arbitration inside one 20 ms microphone frame.
_INTRA_OP_THREADS = 4


class SmartTurnArbiter(TurnArbiter):
    """smart-turn v3.1 on onnxruntime, on the CPU."""

    def __init__(self, session: Any, extractor: Any) -> None:
        self._session = session
        self._extractor = extractor
        self._lock = threading.Lock()

    def probability(self, audio: npt.NDArray[np.float32]) -> float:
        with self._lock:
            features = self._features(audio)
            outputs = self._session.run(None, {"input_features": features})
        return float(outputs[0][0].item())

    def _features(self, audio: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        # The model's reference preprocessing: the padding side and the normalisation
        # both change the answer.
        clipped = audio[-WINDOW_SAMPLES:] if audio.size > WINDOW_SAMPLES else audio
        batch = self._extractor(
            clipped,
            sampling_rate=CAPTURE_SAMPLE_RATE,
            return_tensors="np",
            padding="max_length",
            max_length=WINDOW_SAMPLES,
            truncation=True,
            do_normalize=True,
        )
        features: npt.NDArray[np.float32] = batch.input_features.squeeze(0).astype(np.float32)
        return np.expand_dims(features, axis=0)


def open_smart_turn() -> SmartTurnArbiter:
    """Open smart-turn from its file on disk; it raises if the file isn't there.

    Blocking (the libraries import and the session builds), so it is called on a thread.
    """
    import onnxruntime as ort
    from transformers import WhisperFeatureExtractor

    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = _INTRA_OP_THREADS
    session = ort.InferenceSession(
        model_files.smart_turn_path(), sess_options=options, providers=["CPUExecutionProvider"]
    )
    # chunk_length=8 makes the features (80, 800), the shape the model takes; the
    # default of 30 s would make them (80, 3000). The constructor is unannotated.
    extractor = WhisperFeatureExtractor(chunk_length=8)  # type: ignore[no-untyped-call]
    return SmartTurnArbiter(session, extractor)
