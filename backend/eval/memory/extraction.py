"""What a model files from a conversation, and how much of it is invented.

The product's own extraction request and parser, then its filing rule (proactive
mode, the confidence threshold, the secrets filter) on what came back. Two columns:
what the model proposed, and what would be saved active without anyone reviewing it.
A case with `read` turns is a part of a conversation read after them: they are learned
from first, as the worker would have, then go with the part as context, as learning
sends them, and what learning filed from them (`learned`) is held against the part.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.services.memory.extraction import (
    Filing,
    extraction_messages,
    filing_for,
    kept_profile,
    parse_extraction,
    session_transcript,
    sift,
)
from app.services.memory.models import ExtractedFact, Extraction, LearningMode
from app.services.memory.sensitive import is_sensitive
from eval.common import ratio, read_jsonl, says
from eval.turn import run_turn

GOLD = Path(__file__).with_name("gold.jsonl")

RULES = """How a filed memory is scored:
- a gold line names the words (`must`, `any_of`) that identify its claim, and a memory
  makes that claim when it says all of them;
- a line may also name words that must be absent (`none_of`), which is how a trap for
  "has a dog" leaves "would call a future dog Biscuit" alone;
- in a case read after earlier turns (`read`), a memory that restates them is false:
  it was learned when they were read;
- traps are checked first: a memory matching a trap is a false memory even if it also
  matches an expectation;
- `allowed` lines are true claims a model may file or leave out: they count toward
  neither recall nor false memories;
- a memory matching nothing is a false memory: an invention nobody wrote a trap for
  is the worst case, not an unscored one;
- one memory meets at most one expectation; a second filing of a met one is an extra,
  which costs precision but isn't false;
- precision is the share of filed memories that met an expectation or were allowed,
  and None when nothing was filed."""


@dataclass(slots=True)
class Scored:
    filed: list[str]
    expected: int
    met: int = 0
    allowed: int = 0
    extra: int = 0
    false_memories: list[str] = field(default_factory=list)


def load_gold(path: Path = GOLD) -> list[dict[str, Any]]:
    cases = read_jsonl(path)
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("gold ids must be unique")
    for case in cases:
        if not case["turns"]:
            raise ValueError(f"{case['id']}: a case needs a conversation")
        for line in [*case["expected"], *case["traps"], *case.get("allowed", [])]:
            if not line.get("must") and not line.get("any_of"):
                raise ValueError(f"{case['id']}: every gold line names the words of its claim")
    return cases


def proposed(extraction: Extraction) -> list[str]:
    """Everything the model offered, before any rule of the product's applies."""
    return [f"{key}: {value}" for key, value in extraction.profile.items()] + [
        fact.content for fact in extraction.facts
    ]


def filed_active(
    extraction: Extraction,
    threshold: float,
    cap: int,
    turns: list[dict[str, str]],
    already: list[str] | None = None,
    *,
    earlier: list[dict[str, str]] | None = None,
    learned: list[str] | None = None,
) -> list[str]:
    """What proactive learning would save active: the product's rule, in its order.
    `already` is what the person asked her to keep that day (a case's tool saves), which
    a copy of isn't filed; `earlier`, the turns `turns` went with, and `learned`, what
    learning filed from them."""
    profile = kept_profile(extraction.profile, turns)
    filed = [
        f"{key}: {value}" for key, value in list(profile.items())[:cap] if not is_sensitive(value)
    ]
    kept = already or []
    facts = sift(extraction.facts, turns, kept, earlier=earlier or [], learned=learned or [])
    return filed + _filing(facts.facts[:cap], threshold, (Filing.store,))


def _filing(facts: list[ExtractedFact], threshold: float, ways: tuple[Filing, ...]) -> list[str]:
    """The facts proactive learning files one of `ways`, sensitive ones left out."""
    return [
        fact.content
        for fact in facts
        if filing_for(fact.content, fact.confidence, fact.kind, LearningMode.proactive, threshold)
        in ways
        and not is_sensitive(fact.content)
    ]


def _claims(text: str, line: dict[str, Any]) -> bool:
    return says(text, line.get("must", []), line.get("any_of", []), line.get("none_of", []))


def score(case: dict[str, Any], filed: list[str]) -> Scored:
    result = Scored(filed=filed, expected=len(case["expected"]))
    open_lines = list(case["expected"])
    for memory in filed:
        if any(_claims(memory, trap) for trap in case["traps"]):
            result.false_memories.append(memory)
            continue
        line = next((line for line in open_lines if _claims(memory, line)), None)
        if line is not None:
            open_lines.remove(line)
            result.met += 1
        elif any(_claims(memory, line) for line in case["expected"]):
            result.extra += 1
        elif any(_claims(memory, line) for line in case.get("allowed", [])):
            result.allowed += 1
        else:
            result.false_memories.append(memory)
    return result


async def run_case(
    llm: OllamaLLMClient,
    model: str,
    case: dict[str, Any],
    threshold: float,
    cap: int,
    temperature: float | None = None,
) -> dict[str, Any]:
    turns, read = case["turns"], case.get("read", [])
    learned = await _learned(llm, model, read, threshold, cap, temperature) if read else []
    # At learning's own temperature, as the product asks (`memory.learning_temperature`).
    messages = extraction_messages(session_transcript(turns), session_transcript(read))
    turn = await run_turn(llm, messages, model=model, temperature=temperature)
    # A reply with no JSON, or facts that aren't a list, is a failed extraction: the
    # session's learning is lost. A fact that can't be read is dropped on its own.
    try:
        extraction, parsed = parse_extraction(turn.text), True
    except (ValueError, ValidationError):
        extraction, parsed = Extraction(), False
    return {
        "id": case["id"],
        "parsed": parsed,
        "malformed": extraction.malformed,
        "reply": turn.text.strip()[:800],
        "proposed": asdict(score(case, proposed(extraction))),
        "learned": learned,
        "filed": asdict(
            score(
                case,
                filed_active(
                    extraction,
                    threshold,
                    cap,
                    turns,
                    case.get("already"),
                    earlier=read,
                    learned=learned,
                ),
            )
        ),
    }


async def _learned(
    llm: OllamaLLMClient,
    model: str,
    read: list[dict[str, str]],
    threshold: float,
    cap: int,
    temperature: float | None,
) -> list[str]:
    """What learning filed from `read`, the part before a case's, read on its own
    first: stored or queued for review, as `MemoryStore.recent.learned` lists them."""
    messages = extraction_messages(session_transcript(read))
    turn = await run_turn(llm, messages, model=model, temperature=temperature)
    try:
        extraction = parse_extraction(turn.text)
    except (ValueError, ValidationError):
        return []
    facts = sift(extraction.facts, read, []).facts[:cap]
    return _filing(facts, threshold, (Filing.store, Filing.queue))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "parse_failures": sum(1 for result in results if not result["parsed"]),
        "malformed_facts": sum(result.get("malformed", 0) for result in results),
    }
    for column in ("proposed", "filed"):
        scored = [result[column] for result in results]
        filed = sum(len(item["filed"]) for item in scored)
        false = sum(len(item["false_memories"]) for item in scored)
        summary[f"{column}_false_memories"] = false
        summary[f"{column}_recall"] = ratio(
            sum(item["met"] for item in scored), sum(item["expected"] for item in scored)
        )
        right = sum(item["met"] + item["allowed"] for item in scored)
        summary[f"{column}_precision"] = ratio(right, filed)
    summary["cases"] = results
    return summary
