"""A cross-encoder: how well does a passage answer a question?

The search by meaning embeds the question and each passage apart, so a passage about
the same thing scores as close as one that answers ("sourdough" against the focaccia's
bake). A cross-encoder reads the two together. This one is ms-marco MiniLM-L-6
(`cross-encoder/ms-marco-MiniLM-L-6-v2`, Apache-2.0), in the O2-optimised ONNX export
its repository ships, on onnxruntime on the CPU: about 7 ms a passage on four threads,
75 ms for the ten candidates a question's search brings (median, measured on the
retrieval evaluation).

Its output is a logit, not a probability; the relevance floor is set on that scale. It
was trained on English. It reads other languages in the Latin script passably (a right
passage above a wrong one in French, German and Spanish) and Arabic or Chinese not at
all (right and wrong scored alike), which `services/memory/rag_rerank.py` checks before
asking it.

A CPU model, like smart-turn, so it is opened here and not through the GPU manager. It
is opened once and shared: the ONNX session and the tokenizer are shared state, so one
lock covers both.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from app.adapters import model_files
from app.adapters.base import Reranker

# The model's window, question and passage together. A longer pair is cut from the
# longer of the two, so a pasted question can't leave no room for the passage.
_MAX_TOKENS = 512
# As smart-turn: four intra-op threads keep ten passages under a tenth of a second.
_INTRA_OP_THREADS = 4


class CrossEncoderReranker(Reranker):
    """ms-marco MiniLM-L-6 on onnxruntime, on the CPU."""

    def __init__(self, session: Any, tokenizer: Any) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._lock = threading.Lock()

    def scores(self, question: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        with self._lock:
            encoded = self._tokenizer.encode_batch([(question, passage) for passage in passages])
            feeds = {
                "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64),
            }
            logits = self._session.run(None, feeds)[0]
        return [float(logit) for logit in logits[:, 0]]


def open_reranker() -> CrossEncoderReranker:
    """Open the cross-encoder from its files on disk; it raises if they aren't there.

    Blocking (the libraries import and the session builds), so it is called on a thread.
    """
    import onnxruntime as ort
    from tokenizers import Tokenizer

    folder = Path(model_files.reranker_path())
    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = _INTRA_OP_THREADS
    session = ort.InferenceSession(
        str(folder / model_files.RERANKER_MODEL),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    tokenizer = Tokenizer.from_file(str(folder / model_files.RERANKER_TOKENIZER))
    tokenizer.enable_truncation(max_length=_MAX_TOKENS, strategy="longest_first")
    tokenizer.enable_padding(pad_id=tokenizer.token_to_id("[PAD]"), pad_token="[PAD]")
    return CrossEncoderReranker(session, tokenizer)
