"""One encoder pass for a final transcript's language detection and its decode.

faster-whisper 1.2.1's `transcribe(language=None)` encodes the first thirty-second
window to detect the language (`detect_language`), throws that encoding away, and
encodes the window again to decode it (`generate_segments` encodes its first window
itself unless it is handed one, and every later window regardless). Here the
detection's encoding is handed to the decode, so the first window is encoded once. On
the development machine's GPU that took the median final from 201 ms to 119 ms (56
paired decodes of the call probe's questions, 0.15.4).

Under thirty seconds the two windows differ by one mel frame: the detection's covers
every frame, where `transcribe` decodes from all but the last. On the call probe's
eight recorded questions that changed no language and no transcript
(`tests/test_stt_one_pass.py`, slow). From thirty seconds on, the windows are the same.

The options are what `transcribe` builds for this adapter's call: the beam, the
language, `condition_on_previous_text=False` and every other argument at its default.
That is why faster-whisper is pinned exactly, and `tests/test_stt.py` holds `_options`
to what `transcribe` itself builds. The imports are inside the functions, as in
`whisper_loader`: this module is imported with the adapter, and faster-whisper brings
CTranslate2 and its own cuDNN, which must load after torch's (`model_files`).
"""

from __future__ import annotations

import inspect
from typing import Any

import numpy as np
import numpy.typing as npt


def encode_once(model: Any, audio: npt.NDArray[np.float32]) -> tuple[Any, Any]:
    """The features of the whole buffer, and its first window encoded once."""
    from faster_whisper.audio import pad_or_trim

    features = model.feature_extractor(audio)
    window = pad_or_trim(features[..., : model.feature_extractor.nb_max_frames])
    return features, model.encode(window)


def detect(model: Any, encoded: Any) -> str:
    """The language `detect_language` names for one window: its top language."""
    if not model.model.is_multilingual:
        return "en"
    token, _probability = model.model.detect_language(encoded)[0][0]
    return str(token)[2:-2]  # "<|en|>" -> "en"


def decode(model: Any, features: Any, encoded: Any, language: str, beam: int) -> str:
    """The transcript in `language`, decoded from the encoding `encode_once` made."""
    from faster_whisper.tokenizer import Tokenizer

    tokenizer = Tokenizer(
        model.hf_tokenizer, model.model.is_multilingual, task="transcribe", language=language
    )
    options = _options(tokenizer, beam)
    segments = model.generate_segments(features, tokenizer, options, False, encoded)
    return "".join(segment.text for segment in segments)


def _options(tokenizer: Any, beam: int) -> Any:
    """`transcribe`'s options for `beam_size=beam, condition_on_previous_text=False`,
    every other argument at its 1.2.1 default."""
    from faster_whisper import WhisperModel
    from faster_whisper.transcribe import TranscriptionOptions, get_suppressed_tokens

    # The punctuation sets are read off `transcribe`'s signature: they hold full-width
    # marks that are easy to get wrong. The rest are copied, and `tests/test_stt.py`
    # compares the whole set with what `transcribe` builds.
    defaults = inspect.signature(WhisperModel.transcribe).parameters
    return TranscriptionOptions(
        beam_size=beam,
        best_of=5,
        patience=1,
        length_penalty=1,
        repetition_penalty=1,
        no_repeat_ngram_size=0,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        condition_on_previous_text=False,
        prompt_reset_on_temperature=0.5,
        temperatures=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        initial_prompt=None,
        prefix=None,
        suppress_blank=True,
        suppress_tokens=get_suppressed_tokens(tokenizer, [-1]),
        without_timestamps=False,
        max_initial_timestamp=1.0,
        word_timestamps=False,
        prepend_punctuations=defaults["prepend_punctuations"].default,
        append_punctuations=defaults["append_punctuations"].default,
        multilingual=False,
        max_new_tokens=None,
        clip_timestamps="0",
        hallucination_silence_threshold=None,
        hotwords=None,
    )
