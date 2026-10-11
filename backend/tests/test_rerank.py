"""Reranking document search: the cross-encoder adapter, the service that asks it and
falls back when it can't, and the gate that reads its score."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.adapters.base import Reranker
from app.adapters.rerank.cross_encoder import CrossEncoderReranker, open_reranker
from app.core.config_memory import MemorySettings
from app.core.event_bus import InProcessEventBus
from app.services.chat.context import grounding
from app.services.memory.models import RagChunk, RetrievedChunk
from app.services.memory.rag import RagService
from app.services.memory.rag_rerank import Reranking
from app.services.memory.rag_store import RagStore
from app.services.memory.store import MemoryStore
from tests.fakes import FakeEmbedder, build_fake_memory_stack


class _Tokenizer:
    """Three token ids a pair; records the pairs it was given."""

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    def encode_batch(self, pairs: list[tuple[str, str]]) -> list[SimpleNamespace]:
        self.pairs.extend(pairs)
        return [
            SimpleNamespace(ids=[101, n, 102], attention_mask=[1, 1, 1], type_ids=[0, 0, 1])
            for n in range(len(pairs))
        ]


class _Session:
    def __init__(self, logits: list[float]) -> None:
        self.logits = logits
        self.feeds: list[dict[str, Any]] = []

    def run(self, _names: Any, feeds: dict[str, Any]) -> list[Any]:
        self.feeds.append(feeds)
        return [np.array([[value] for value in self.logits], dtype=np.float32)]


class _Model(Reranker):
    """Scores a passage by its length, and records what it read."""

    def __init__(self, *, fail: bool = False) -> None:
        self.read: list[tuple[str, list[str]]] = []
        self.fail = fail

    def scores(self, question: str, passages: list[str]) -> list[float]:
        self.read.append((question, passages))
        if self.fail:
            raise RuntimeError("the session failed")
        return [float(len(passage)) for passage in passages]


def _hit(chunk_id: int, text: str, *, path: str = "", distance: float = 0.3) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=RagChunk(id=chunk_id, document_id=1, ordinal=chunk_id, text=text, heading_path=path),
        document_title="notes",
        distance=distance,
    )


def test_the_model_scores_each_pair_in_order() -> None:
    tokenizer, session = _Tokenizer(), _Session([2.5, -7.0])

    scores = CrossEncoderReranker(session, tokenizer).scores(
        "How often?", ["Every 8 weeks.", "Cash only."]
    )

    assert scores == pytest.approx([2.5, -7.0])
    assert tokenizer.pairs == [("How often?", "Every 8 weeks."), ("How often?", "Cash only.")]
    feeds = session.feeds[0]
    assert set(feeds) == {"input_ids", "attention_mask", "token_type_ids"}
    assert all(array.dtype == np.int64 and array.shape == (2, 3) for array in feeds.values())


def test_no_passages_ask_nothing_of_the_model() -> None:
    session = _Session([])

    assert CrossEncoderReranker(session, _Tokenizer()).scores("q", []) == []
    assert session.feeds == []


@pytest.mark.slow
def test_the_installed_model_prefers_the_passage_that_answers() -> None:
    """The real files and libraries (7.4 against -11.4 when measured)."""
    right, wrong = open_reranker().scores(
        "How often should the coffee machine be descaled?",
        [
            "Care › Descaling\n\nDescale the machine every 8 weeks.",
            "Travel › Money\n\nSmall cafés prefer cash.",
        ],
    )

    assert right > 0 > wrong


async def test_reranking_orders_by_score_and_keeps_it() -> None:
    hits = [_hit(1, "short"), _hit(2, "a much longer passage")]

    ranked = await Reranking(lambda: _Model()).rerank("question?", hits)

    assert [hit.chunk.id for hit in ranked] == [2, 1]
    assert [hit.relevance for hit in ranked] == [21.0, 5.0]


async def test_the_model_reads_each_passage_with_its_headings() -> None:
    """As it was embedded: given the text alone, the right passage came first for 81% of
    the evaluation's questions, and with its path for 92% (mutation: the text alone)."""
    model = _Model()
    hits = [_hit(1, "Bake at 230.", path="recipes › Focaccia"), _hit(2, "No path.")]

    await Reranking(lambda: model).rerank("When?", hits)

    assert model.read == [("When?", ["recipes › Focaccia\n\nBake at 230.", "No path."])]


async def test_a_failing_model_leaves_the_search_as_it_was() -> None:
    hits = [_hit(1, "a"), _hit(2, "bb")]

    assert await Reranking(lambda: _Model(fail=True)).rerank("q?", hits) == hits


async def test_a_model_that_wont_open_is_tried_once() -> None:
    """Missing from disk: search goes on without it, and doesn't look again every
    question (mutation: retry)."""
    tries = 0

    def opener() -> Reranker:
        nonlocal tries
        tries += 1
        raise FileNotFoundError("not on disk")

    reranking = Reranking(opener)
    hits = [_hit(1, "a")]

    assert await reranking.rerank("q?", hits) == hits
    assert await reranking.rerank("q?", hits) == hits
    assert tries == 1


async def test_the_model_opens_once_for_questions_at_the_same_time() -> None:
    opened = 0
    model = _Model()

    def opener() -> Reranker:
        nonlocal opened
        opened += 1
        return model

    reranking = Reranking(opener)
    await asyncio.gather(*(reranking.rerank("q?", [_hit(1, "a")]) for _ in range(3)))

    assert opened == 1


@pytest.mark.parametrize(
    ("question", "text"),
    [
        ("Как часто чистить кофемашину?", "Descale the machine every 8 weeks."),
        ("How often is it descaled?", "咖啡机每八周除垢一次。"),
    ],
)
async def test_text_the_model_cannot_read_stays_as_the_search_found_it(
    question: str, text: str
) -> None:
    """In Arabic and Chinese it scored a wrong passage as high as the right one: the
    multilingual search's order stands (mutation: rerank everything)."""
    model = _Model()
    hits = [_hit(1, text), _hit(2, "Other text entirely, and somewhat longer.")]

    assert await Reranking(lambda: model).rerank(question, hits) == hits
    assert model.read == []


async def test_a_few_letters_of_another_script_are_still_read() -> None:
    model = _Model()

    await Reranking(lambda: model).rerank(
        "What is π for?", [_hit(1, "The ratio π of a circle to its width, about 3.14159.")]
    )

    assert model.read


async def _library(tmp_path: Path, depth: int, model: _Model) -> tuple[MemoryStore, RagService]:
    store, _, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    settings = MemorySettings(rag_rerank_depth=depth)
    rag = RagService(
        RagStore(store, FakeEmbedder.DIM),
        FakeEmbedder(),
        InProcessEventBus(),
        settings,
        Reranking(lambda: model),
    )
    await rag.init_schema()
    # Nearest to "coffee" in this order; the fake model prefers the longest.
    texts = ("Coffee.", "Coffee and rain.", "Coffee, rain and a cold wet weather week.", "Mom.")
    for n, text in enumerate(texts):
        await rag.index_text(f"d{n}", text)
    return store, rag


async def test_search_reranks_its_first_candidates_then_keeps_what_was_asked(
    tmp_path: Path,
) -> None:
    """Three read though one was asked for (mutation: rerank only what is returned)."""
    model = _Model()
    store, rag = await _library(tmp_path, 3, model)

    hits = await rag.retrieve("coffee", k=1)

    assert len(model.read[0][1]) == 3
    assert [hit.chunk.text for hit in hits] == ["Coffee, rain and a cold wet weather week."]
    await store.close()


async def test_with_a_depth_of_zero_nothing_is_reranked(tmp_path: Path) -> None:
    model = _Model()
    store, rag = await _library(tmp_path, 0, model)

    hits = await rag.retrieve("coffee", k=1)

    assert model.read == []
    assert [hit.chunk.text for hit in hits] == ["Coffee."]
    assert hits[0].relevance is None
    await store.close()


def test_a_reranked_chunk_is_judged_by_its_score_not_its_similarity() -> None:
    """ "Sourdough" against the focaccia's bake: near by meaning, no answer (mutation: the
    similarity floor for every chunk)."""
    settings = MemorySettings()
    near = _hit(1, "The focaccia bakes at 230.", distance=0.05).model_copy(
        update={"relevance": -9.0}
    )
    far = _hit(2, "The sourdough bakes at 250.", distance=0.6).model_copy(update={"relevance": 3.0})

    assert grounding([near, far], settings, "What temperature for the sourdough?") == [far]


def test_a_reranked_chunk_naming_the_code_is_cited_whatever_its_score() -> None:
    code = _hit(1, "Error E15: the drip tray is full.").model_copy(update={"relevance": -9.0})

    assert grounding([code], MemorySettings(), "What does E15 mean?") == [code]
