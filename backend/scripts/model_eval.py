"""Evaluate language models on Veronica's own prompts: tool selection, what they learn
from a conversation, answer accuracy, and whether they carry out instructions planted in
what she reads. Results go to backend/eval/results.

    uv run python -m scripts.model_eval --models qwen3.5:9b,qwen3:8b --runs 3
    uv run python -m scripts.model_eval --families tools --limit 5   # a smoke run

Each model gets an untimed warm-up turn first, so no case pays for loading it. The
scoring rules are printed before the first case. A run with --limit is a smoke run:
it prints its numbers but writes no result file, so the results folder only ever
holds full runs.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path
from typing import Any

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.core.config import Settings, get_settings
from app.core.version import APP_VERSION
from eval.accuracy import answers
from eval.common import file_hash, median_of, result_path, write_result
from eval.injection import attacks, scoring
from eval.memory import extraction
from eval.tools import gate
from eval.turn import run_turn

FAMILIES = ("tools", "memory", "accuracy", "injection")
RULES = {
    "tools": gate.RULES,
    "memory": extraction.RULES,
    "accuracy": answers.RULES,
    "injection": scoring.RULES,
}
HEADLINES: dict[str, tuple[str, ...]] = {
    "tools": (
        "selection",
        "wrong_tool",
        "silent",
        "false_call",
        "args_valid",
        "first_ms_median",
        "call_selection",
        "call_silent",
        "call_false_call",
    ),
    "memory": (
        "filed_false_memories",
        "filed_recall",
        "filed_precision",
        "proposed_false_memories",
        "proposed_recall",
        "parse_failures",
    ),
    "accuracy": ("overall", "fact", "grounded", "memory", "instruction"),
    "injection": (
        "obeyed",
        "ran_unasked",
        "stopped",
        "echoed",
        "echo_caveated",
        "false_call",
        "unscored",
        "first_ms_median",
        "call_obeyed",
        "call_ran_unasked",
        "call_echoed",
        "call_echo_caveated",
        "call_false_call",
    ),
}


async def run_family(
    family: str, llm: OllamaLLMClient, model: str, settings: Settings, limit: int | None
) -> tuple[dict[str, Any], Path]:
    if family == "tools":
        cases = gate.load_cases()[:limit]
        with tempfile.TemporaryDirectory(prefix="veronica-eval-") as folder:
            root = Path(folder).resolve()
            gate.build_tree(root)
            lanes = {}
            for spoken in (False, True):
                surface = gate.tool_surface(settings, root, spoken=spoken)
                outcomes = [await gate.run_case(llm, model, case, surface, root) for case in cases]
                lanes[spoken] = gate.summarize(outcomes, cases)
        # A call's lane beside chat's, its keys prefixed: the spoken prompt moves the model.
        call = {f"call_{key}": value for key, value in lanes[True].items()}
        return {**lanes[False], **call}, gate.CASES
    if family == "memory":
        gold = extraction.load_gold()[:limit]
        threshold = settings.memory.learning_confidence_threshold
        cap = settings.memory.learning_max_facts
        warmth = settings.memory.learning_temperature
        results = [
            await extraction.run_case(llm, model, case, threshold, cap, warmth) for case in gold
        ]
        # Kept with the result: the same model at another temperature is another result.
        return {**extraction.summarize(results), "temperature": warmth}, extraction.GOLD
    if family == "injection":
        planted = attacks.load_cases()[:limit]
        with tempfile.TemporaryDirectory(prefix="veronica-eval-") as folder:
            root = Path(folder).resolve()
            on = attacks.surface(settings, root)
            found = [await attacks.run_case(llm, model, case, on) for case in planted]
            # A call reads her memory and files, never a document, on its own prompt.
            call_on = attacks.surface(settings, root, spoken=True)
            heard = [case for case in planted if case["vector"] != "document"]
            call_found = [await attacks.run_case(llm, model, case, call_on) for case in heard]
            call = {f"call_{key}": value for key, value in scoring.summarize(call_found).items()}
            return {**scoring.summarize(found), **scoring.conditions(on), **call}, attacks.CASES
    questions = answers.load_cases()[:limit]
    graded = [await answers.run_case(llm, model, case) for case in questions]
    return answers.summarize(graded), answers.CASES


def _cell(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}"


def print_table(medians: dict[tuple[str, str], dict[str, float | None]], models: list[str]) -> None:
    print("\nMedians across runs")
    print(f"{'':34}" + "".join(f"{model:>16}" for model in models))
    for family in FAMILIES:
        for key in HEADLINES[family]:
            cells = [medians.get((family, model), {}).get(key) for model in models]
            if all(cell is None for cell in cells) and (family, models[0]) not in medians:
                continue
            print(f"{family + '.' + key:34}" + "".join(f"{_cell(cell):>16}" for cell in cells))


async def evaluate(models: list[str], families: list[str], runs: int, limit: int | None) -> None:
    settings = get_settings()
    llm = OllamaLLMClient(settings.llm)
    medians: dict[tuple[str, str], dict[str, float | None]] = {}
    try:
        for family in families:
            print(f"[{family}] {RULES[family]}\n")
        for model in models:
            await run_turn(llm, [{"role": "user", "content": "Say OK."}], model=model)
            for family in families:
                summaries = []
                for run in range(1, runs + 1):
                    summary, cases_path = await run_family(family, llm, model, settings, limit)
                    summaries.append(summary)
                    headline = ", ".join(f"{key}={summary.get(key)}" for key in HEADLINES[family])
                    print(f"{family} | {model} | run {run}: {headline}")
                    if limit is None:
                        record = {
                            "version": APP_VERSION,
                            "family": family,
                            "model": model,
                            "run": run,
                            "cases_hash": file_hash(cases_path),
                            **summary,
                        }
                        write_result(result_path(APP_VERSION, family, model), record)
                medians[(family, model)] = {
                    key: median_of(summaries, key) for key in HEADLINES[family]
                }
        print_table(medians, models)
    finally:
        await llm.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", default=get_settings().llm.model, help="comma-separated")
    parser.add_argument("--families", default=",".join(FAMILIES), help="comma-separated")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None, help="first N cases only (smoke)")
    args = parser.parse_args()
    families = [family for family in args.families.split(",") if family]
    unknown = sorted(set(families) - set(FAMILIES))
    if unknown:
        parser.error(f"unknown families: {', '.join(unknown)}")
    models = [model for model in args.models.split(",") if model]
    asyncio.run(evaluate(models, families, args.runs, args.limit))


if __name__ == "__main__":
    main()
