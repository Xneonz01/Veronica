"""Does document search find the passage that answers? Veronica's own path over a small
fictional library (`corpus/`), with the real embedding model and no language model.

- A hit is a retrieved chunk of the case's document (`doc`, or one it names under
  `also` because it answers as well) that holds the case's `answer` phrase, with case,
  width and spacing folded. The same phrase elsewhere is no answer: "within 30 days"
  is in the coffee machine's warranty as well as the insurance claim rules.
- `rank` is where the first hit came in the ranked list (the top `DEPTH`), and `cited`
  whether a hit was among what a chat turn would put in front of the model: the same
  call a turn makes (`retrieve` for `memory.rag_top_k`), then `context.grounding`.
- A no-answer case is right when nothing is cited: its `probe`, a phrase an answer
  would need, is in no chunk, and the gold check refuses to run if one ever is.
- The kinds: `exact` names an identifier or a number its passage holds; `paraphrase`
  asks in other words; `heading` asks about a passage whose subject only its section
  heading names; `no_answer` has no answer in the library.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata
from pathlib import Path
from statistics import mean, median
from typing import Any

from app.core.config_memory import MemorySettings
from app.services.chat.context import grounding
from app.services.memory.models import RetrievedChunk
from app.services.memory.rag import RagService
from app.services.memory.store import MemoryStore
from eval.common import percentile, ratio, read_jsonl

CORPUS = Path(__file__).with_name("corpus")
CASES = Path(__file__).with_name("cases.jsonl")
KINDS = ("exact", "paraphrase", "heading", "no_answer")
DEPTH = 10  # chunks ranked per question: MRR is read at 10
# A timed search waits this long first, as a question comes after a pause: the GPU
# drops its clocks when idle, and the question's embedding then takes 50 ms, not 5.
PAUSE_S = 1.0

RULES = """How retrieval is scored:
- a hit is a retrieved chunk of the case's document holding the case's answer phrase;
- recall@k: a hit in the top k; mrr: 1/rank of the first hit in the top 10, else 0;
- cited: a hit among the chunks a chat turn would cite (relevance floor, then budget);
- a no-answer question is right when nothing is cited ("grounded" counts the wrong)."""

_SPACE = re.compile(r"\s+")


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    cases = read_jsonl(path)
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    for case in cases:
        if case["kind"] not in KINDS:
            raise ValueError(f"{case['id']}: unknown kind {case['kind']!r}")
        field = "probe" if case["kind"] == "no_answer" else "answer"
        if not case.get(field):
            raise ValueError(f"{case['id']}: needs {field!r}")
    return cases


def folded(text: str) -> str:
    """Chunking joins a paragraph's lines, so a copied answer is matched loosely."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip().casefold()


def holds(text: str, needle: str) -> bool:
    return folded(needle) in folded(text)


def answers(case: dict[str, Any], title: str, text: str) -> bool:
    """Whether a chunk of the document `title` answers `case`."""
    answer = case.get("answer")
    documents = {case.get("doc"), *case.get("also", [])}
    return answer is not None and title in documents and holds(text, answer)


def corpus_hash() -> str:
    """Stamped into every result beside the cases' hash: a number is tied to the
    library it was measured on, too."""
    digest = hashlib.sha256()
    for path in sorted(CORPUS.iterdir()):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


async def index_corpus(rag: RagService) -> None:
    """Every document, under the title an upload gets: its file name without the
    extension."""
    for path in sorted(CORPUS.iterdir()):
        await rag.index_file(path, title=path.stem)


async def chunk_texts(store: MemoryStore) -> list[tuple[str, str]]:
    """Every stored chunk as (its document's title, its text)."""
    async with store.db.execute(
        "SELECT d.title, c.text FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id"
    ) as cur:
        return [(str(row[0]), str(row[1])) for row in await cur.fetchall()]


def check_gold(cases: list[dict[str, Any]], chunks: list[tuple[str, str]]) -> None:
    """Refuse to score a case nobody could answer, or a no-answer case the library
    answers after all: either would read as a retrieval result and mean nothing."""
    unfound = [
        case["id"]
        for case in cases
        if case["kind"] != "no_answer"
        and not any(answers(case, title, text) for title, text in chunks)
    ]
    planted = [
        case["id"]
        for case in cases
        if case["kind"] == "no_answer" and any(holds(text, case["probe"]) for _, text in chunks)
    ]
    if unfound or planted:
        raise ValueError(f"gold is wrong: no chunk answers {unfound}; probes found {planted}")


def _answers(case: dict[str, Any], hit: RetrievedChunk) -> bool:
    return answers(case, hit.document_title, hit.chunk.text)


async def run_case(
    rag: RagService, settings: MemorySettings, case: dict[str, Any]
) -> dict[str, Any]:
    question = case["question"]
    ranked = await rag.retrieve(question, k=DEPTH)
    # A turn's own call, not the deeper ranking cut short: with a setting below DEPTH
    # (the keyword depth, say) the two can put different chunks on top.
    cited = grounding(await rag.retrieve(question, k=settings.rag_top_k), settings, question)
    rank = next((i for i, hit in enumerate(ranked, 1) if _answers(case, hit)), None)
    return {
        "id": case["id"],
        "kind": case["kind"],
        "rank": rank,
        "cited": any(_answers(case, hit) for hit in cited),
        "cited_count": len(cited),
    }


async def time_turns(
    searches: dict[str, RagService], cases: list[dict[str, Any]], top_k: int
) -> dict[str, dict[str, float | None]]:
    """How long a turn's search takes under each of `searches`, each question asked of
    all of them in turn, the order alternating. Measured apart, two runs put the GPU in
    different states and differed by twice what reranking costs; interleaved, the
    difference is the reranking."""
    waits: dict[str, list[float]] = {name: [] for name in searches}
    for number, case in enumerate(cases):
        names = list(searches) if number % 2 == 0 else list(reversed(searches))
        for name in names:
            await asyncio.sleep(PAUSE_S)
            started = time.perf_counter()
            await searches[name].retrieve(case["question"], k=top_k)
            waits[name].append((time.perf_counter() - started) * 1000.0)
    return {
        # The median as every p50 is (the middle two averaged), not the lower of them.
        name: {"median_ms": _median(values), "p90_ms": percentile(values, 0.9)}
        for name, values in waits.items()
    }


def _median(values: list[float]) -> float | None:
    return round(median(values), 1) if values else None


def _recall(results: list[dict[str, Any]], k: int) -> float | None:
    return ratio(sum(1 for r in results if r["rank"] is not None and r["rank"] <= k), len(results))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [r for r in results if r["kind"] != "no_answer"]
    unanswerable = [r for r in results if r["kind"] == "no_answer"]
    summary: dict[str, Any] = {
        "cases": len(results),
        "recall_at_1": _recall(answerable, 1),
        "recall_at_3": _recall(answerable, 3),
        "recall_at_5": _recall(answerable, 5),
        "mrr_at_10": (
            round(mean(1 / r["rank"] if r["rank"] else 0.0 for r in answerable), 4)
            if answerable
            else None
        ),
        "cited": ratio(sum(1 for r in answerable if r["cited"]), len(answerable)),
        "cited_per_question": (
            round(mean(r["cited_count"] for r in answerable), 2) if answerable else None
        ),
        "no_answer_grounded": ratio(
            sum(1 for r in unanswerable if r["cited_count"]), len(unanswerable)
        ),
    }
    for kind in KINDS[:-1]:
        of_kind = [r for r in answerable if r["kind"] == kind]
        summary[f"{kind}_recall_at_5"] = _recall(of_kind, 5)
        summary[f"{kind}_cited"] = ratio(sum(1 for r in of_kind if r["cited"]), len(of_kind))
    summary["misses"] = [r["id"] for r in answerable if not r["cited"]]
    summary["grounded_ids"] = [r["id"] for r in unanswerable if r["cited_count"]]
    # Each case as scored, so two results can be compared question by question.
    summary["per_case"] = {
        r["id"]: {"rank": r["rank"], "cited": r["cited"], "cited_count": r["cited_count"]}
        for r in results
    }
    return summary
