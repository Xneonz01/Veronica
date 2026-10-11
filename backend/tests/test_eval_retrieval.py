"""The retrieval evaluation's own rules: its library, its gold, and how it scores."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.core.config_memory import MemorySettings
from app.core.event_bus import InProcessEventBus
from app.services.memory.chunking import chunk_text
from app.services.memory.models import RetrievedChunk
from eval.retrieval import search
from tests.fakes import build_fake_memory_stack, build_fake_rag


def test_the_library_and_its_questions_are_the_ones_documented() -> None:
    cases = search.load_cases()
    documents = {path.stem for path in search.CORPUS.iterdir()}

    assert Counter(case["kind"] for case in cases) == {
        "exact": 20,
        "paraphrase": 32,
        "heading": 7,
        "no_answer": 10,
    }
    assert len(documents) == 13
    named = {name for case in cases for name in (case.get("doc"), *case.get("also", [])) if name}
    assert named <= documents


def test_every_answer_is_in_its_own_document_and_no_probe_anywhere() -> None:
    """The gold check, on the chunks indexing would store; no embedder needed."""
    chunks = [
        (path.stem, chunk.text)
        for path in sorted(search.CORPUS.iterdir())
        for chunk in chunk_text(
            path.read_text(encoding="utf-8"), title=path.stem, markdown=path.suffix == ".md"
        )
    ]

    search.check_gold(search.load_cases(), chunks)


def test_a_hit_is_the_answer_in_the_cases_own_document() -> None:
    """The same words in another document are no answer: "within 30 days" is in the
    coffee machine's warranty as well as the insurance claim rules."""
    case = {"doc": "insurance", "answer": "within 30 days"}

    assert search.answers(case, "insurance", "Report a claim WITHIN 30\ndays.")
    assert not search.answers(case, "coffee-guide", "Register it within 30 days.")
    assert search.answers({**case, "also": ["coffee-guide"]}, "coffee-guide", "within 30 days")


def test_the_gold_check_refuses_an_answer_its_document_does_not_hold() -> None:
    case = {"id": "t1", "kind": "paraphrase", "doc": "insurance", "answer": "within 30 days"}

    with pytest.raises(ValueError, match="t1"):
        search.check_gold([case], [("coffee-guide", "Register it within 30 days.")])


def test_the_gold_check_refuses_a_probe_the_library_holds() -> None:
    case = {"id": "n1", "kind": "no_answer", "probe": "sourdough starter"}

    with pytest.raises(ValueError, match="n1"):
        search.check_gold([case], [("recipes", "Feed the Sourdough  starter daily.")])


async def test_a_case_is_ranked_and_cited_the_way_a_turn_cites(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    settings = MemorySettings()
    rag = await build_fake_rag(store, bus, settings)
    await rag.index_text("coffee", "The espresso machine brews at 9 bar.")
    await rag.index_text("weather", "Rain is coming, and a cold night.")
    answerable = {
        "id": "p1",
        "kind": "paraphrase",
        "doc": "coffee",
        "question": "What pressure does the espresso need?",
        "answer": "9 bar",
    }
    unanswerable = {"id": "n1", "kind": "no_answer", "question": "Latte art?", "probe": "x"}

    asked: list[int | None] = []
    retrieve = rag.retrieve

    async def spy(question: str, *, k: int | None = None) -> list[RetrievedChunk]:
        asked.append(k)
        return await retrieve(question, k=k)

    rag.retrieve = spy  # type: ignore[method-assign]

    hit = await search.run_case(rag, settings, answerable)
    grounded = await search.run_case(rag, settings, unanswerable)

    # Ranked ten deep; cited from the call a turn makes (mutation: the ranking cut short).
    assert asked == [search.DEPTH, settings.rag_top_k] * 2
    assert hit == {"id": "p1", "kind": "paraphrase", "rank": 1, "cited": True, "cited_count": 1}
    assert grounded == {
        "id": "n1",
        "kind": "no_answer",
        "rank": None,
        "cited": False,
        "cited_count": 1,  # the espresso note answers nothing about latte art
    }
    await store.close()


def test_the_summary_scores_what_the_cases_scored() -> None:
    results = [
        {"id": "x1", "kind": "exact", "rank": 1, "cited": True, "cited_count": 2},
        {"id": "p1", "kind": "paraphrase", "rank": 3, "cited": True, "cited_count": 1},
        {"id": "p2", "kind": "paraphrase", "rank": None, "cited": False, "cited_count": 0},
        {"id": "n1", "kind": "no_answer", "rank": None, "cited": False, "cited_count": 1},
        {"id": "n2", "kind": "no_answer", "rank": None, "cited": False, "cited_count": 0},
    ]

    summary = search.summarize(results)

    assert summary["cases"] == 5
    assert (summary["recall_at_1"], summary["recall_at_3"]) == (0.3333, 0.6667)
    assert summary["mrr_at_10"] == round((1 + 1 / 3 + 0) / 3, 4)
    assert summary["cited"] == 0.6667
    assert summary["no_answer_grounded"] == 0.5
    assert summary["misses"] == ["p2"] and summary["grounded_ids"] == ["n1"]
    assert summary["per_case"]["p1"] == {"rank": 3, "cited": True, "cited_count": 1}


async def test_turns_are_timed_asking_each_search_in_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each question of every search, the order alternating, so neither always follows
    the other (mutation: time one search after the other)."""
    monkeypatch.setattr(search, "PAUSE_S", 0.0)
    bus = InProcessEventBus()
    store, _, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)
    order: list[str] = []

    class _Search:
        def __init__(self, name: str) -> None:
            self.name = name

        async def retrieve(self, question: str, *, k: int) -> list[RetrievedChunk]:
            order.append(self.name)
            return await rag.retrieve(question, k=k)

    cases = [{"question": "coffee"}, {"question": "rain"}, {"question": "wiring"}]
    searches: dict[str, Any] = {"a": _Search("a"), "b": _Search("b")}

    timed = await search.time_turns(searches, cases, 5)

    assert order == ["a", "b", "b", "a", "a", "b"]
    assert set(timed) == {"a", "b"}
    assert all(set(times) == {"median_ms", "p90_ms"} for times in timed.values())
    await store.close()


def test_a_median_time_is_the_middle_two_averaged() -> None:
    """As every other p50 is; the nearest rank gave the lower of them (mutation: the
    nearest rank)."""
    assert search._median([4.0, 1.0, 3.0, 2.0]) == 2.5
    assert search._median([]) is None
