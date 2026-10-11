"""Reranking document search: a cross-encoder reads each candidate beside the question.

The searches by meaning and by keyword find the candidates; this orders the first
`rag_rerank_depth` of them by how well each answers the question (`adapters/rerank`),
and each keeps its score for the gate (`chat/context.grounding`), which then cites only
those that clear `rag_rerank_floor`. Measured on the retrieval evaluation, 10 deep: the
passage that answers came first for 92% of the questions the library answers (85%
without), and 3 of the 10 it doesn't answer were given passages (6 without).

It fails open, to the searches' own order and the similarity floor: when the model
isn't on disk or fails, and when the question or a candidate isn't in the Latin script.
The model was trained on English, and in Arabic or Chinese it scored a wrong passage as
high as the right one, so reranking there would scramble what the multilingual search
got right.
"""

from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from collections.abc import Callable

from app.adapters.base import Reranker
from app.services.memory.models import RetrievedChunk
from app.telemetry import current_blackbox, current_perf

logger = logging.getLogger(__name__)

# Nine letters in ten: a Greek letter in a formula, or a name in another alphabet, still
# leaves a passage the model reads.
_LATIN_SHARE = 0.9


class Reranking:
    """The cross-encoder, opened on first use, on a thread."""

    def __init__(self, opener: Callable[[], Reranker]) -> None:
        self._opener = opener
        self._model: Reranker | None = None
        self._unavailable = False
        self._opening = asyncio.Lock()

    async def rerank(self, question: str, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """`hits` best answer first, each carrying its score; as they came when the model
        can't read them or can't run."""
        if not hits:
            return hits
        model = await self._open()
        if model is None:
            return hits
        started = time.perf_counter()
        try:
            scores = await asyncio.to_thread(_scores, model, question, hits)
        except Exception as exc:  # a failed rerank must not fail the turn
            logger.exception("document ranking failed")
            current_blackbox().record("warn", "rag.rerank_failed", error=type(exc).__name__)
            return hits
        if scores is None:
            return hits
        elapsed = (time.perf_counter() - started) * 1000.0
        current_perf().log("rag.rerank", elapsed, passages=len(hits))
        order = sorted(range(len(hits)), key=lambda index: -scores[index])
        return [hits[index].model_copy(update={"relevance": scores[index]}) for index in order]

    async def _open(self) -> Reranker | None:
        if self._model is not None or self._unavailable:
            return self._model
        async with self._opening:
            if self._model is None and not self._unavailable:
                try:
                    self._model = await asyncio.to_thread(self._opener)
                except Exception as exc:
                    # Most likely not on disk, which the start-up check has already
                    # named with the command that installs it. Search goes on without.
                    self._unavailable = True
                    logger.warning("document ranking is unavailable: %s", type(exc).__name__)
                    current_blackbox().record(
                        "warn", "rag.rerank_unavailable", error=type(exc).__name__
                    )
        return self._model


def passage(hit: RetrievedChunk) -> str:
    """What the model reads: the heading path and the text, as the chunk was embedded.
    Given the text alone, the right passage came first for 81% of the evaluation's
    questions; with its path, 92%."""
    path = hit.chunk.heading_path
    return f"{path}\n\n{hit.chunk.text}" if path else hit.chunk.text


def _scores(model: Reranker, question: str, hits: list[RetrievedChunk]) -> list[float] | None:
    """The model's scores, or None when the question or a passage isn't in the Latin
    script. On a thread: reading every letter of ten passages is not free."""
    passages = [passage(hit) for hit in hits]
    if not all(_latin(text) for text in (question, *passages)):
        return None
    return model.scores(question, passages)


def _latin(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    latin = sum(1 for ch in letters if unicodedata.name(ch, "").startswith("LATIN"))
    return latin >= _LATIN_SHARE * len(letters)
