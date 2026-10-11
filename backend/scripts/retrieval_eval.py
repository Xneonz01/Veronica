"""Measure document search on the fictional library in backend/eval/retrieval: where the
passage that answers lands, and what a chat turn would cite. No language model; the real
embedding model, loaded through the GPU manager. Results go to backend/eval/results.

    uv run python -m scripts.retrieval_eval
    uv run python -m scripts.retrieval_eval --limit 10   # a smoke run: prints, writes nothing
    uv run python -m scripts.retrieval_eval --timing     # a turn's wait, reranked or not

The library is indexed into a throwaway database each run, never into Veronica's own.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path
from typing import Any

from app.adapters import model_files
from app.core.config import get_settings
from app.core.config_memory import MemorySettings
from app.core.event_bus import InProcessEventBus
from app.core.gpu_manager import CudaGpuModelManager, gib
from app.core.version import APP_VERSION
from app.services.memory.rag import RagService
from app.services.memory.rag_store import RagStore
from app.services.memory.runtime import MemoryRuntime, build_memory_runtime
from eval.common import file_hash, result_path, write_result
from eval.retrieval import search

# The settings a result depends on, recorded with it: `get_settings` reads a local
# `.env`, which could change any of them.
SETTINGS = (
    "rag_top_k",
    "rag_keyword_depth",
    "rag_relevance_floor",
    "rag_rerank_depth",
    "rag_rerank_floor",
    "rag_token_budget",
    "semantic_baseline",
    "chunk_target_chars",
    "chunk_overlap_chars",
)
HEADLINES = (
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "mrr_at_10",
    "cited",
    "cited_per_question",
    "no_answer_grounded",
    "exact_recall_at_5",
    "exact_cited",
    "paraphrase_recall_at_5",
    "paraphrase_cited",
    "heading_recall_at_5",
    "heading_cited",
)


async def measure(limit: int | None, timing: bool) -> dict[str, Any]:
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="veronica-retrieval-") as folder:
        memory = settings.memory.model_copy(update={"db_path": str(Path(folder) / "memory.db")})
        scratch = settings.model_copy(update={"memory": memory})
        manager = CudaGpuModelManager(budget_gib=gib(settings.gpu.vram_budget_gb))
        runtime = build_memory_runtime(scratch, InProcessEventBus(), manager, None)  # scratch
        await runtime.init()
        try:
            await search.index_corpus(runtime.rag)
            cases = search.load_cases()
            search.check_gold(cases, await search.chunk_texts(runtime.store))
            if timing:
                measured = await _timing(runtime, memory, cases[:limit])
            else:
                results = [
                    await search.run_case(runtime.rag, memory, case) for case in cases[:limit]
                ]
                measured = search.summarize(results)
        finally:
            await runtime.store.close()
    return {
        "embedding_model": memory.embedding_model,
        "embedding_device": memory.embedding_device,
        "reranker": model_files.RERANKER_REPO if memory.rag_rerank_depth > 0 else None,
        "settings": {name: getattr(memory, name) for name in SETTINGS},
        **measured,
    }


async def _timing(
    runtime: MemoryRuntime, memory: MemorySettings, cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """The configured search against the same one without reranking, on the same store."""
    plain = RagService(
        RagStore(runtime.store, memory.embedding_dim),
        runtime.embedder,
        InProcessEventBus(),
        memory.model_copy(update={"rag_rerank_depth": 0}),
    )
    await runtime.rag.retrieve(cases[0]["question"])  # opens the models before timing
    searches = {"configured": runtime.rag, "not_reranked": plain}
    return {
        "pause_s": search.PAUSE_S,
        "questions": len(cases),
        **await search.time_turns(searches, cases, memory.rag_top_k),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=None, help="first N cases only (smoke)")
    parser.add_argument(
        "--timing", action="store_true", help="time a turn's search, reranked or not"
    )
    args = parser.parse_args()
    family = "retrieval-timing" if args.timing else "retrieval"
    if not args.timing:
        print(search.RULES, "\n")
    summary = asyncio.run(measure(args.limit, args.timing))
    if args.timing:
        for name in ("configured", "not_reranked"):
            print(f"{name:14} {summary[name]}")
    else:
        for key in HEADLINES:
            print(f"{key:24} {summary.get(key)}")
        print(f"{'misses':24} {summary['misses']}")
        print(f"{'grounded no-answers':24} {summary['grounded_ids']}")
    if args.limit is None:
        record = {
            "version": APP_VERSION,
            "family": family,
            "cases_hash": file_hash(search.CASES),
            "corpus_hash": search.corpus_hash(),
            # The scoring rules change too; a result says which ones it was read by.
            "harness_hash": file_hash(Path(search.__file__)),
            **summary,
        }
        path = result_path(APP_VERSION, family, summary["embedding_model"])
        write_result(path, record)
        print(f"\nwritten to {path.name}")


if __name__ == "__main__":
    main()
