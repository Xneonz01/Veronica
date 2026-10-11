"""Does a model answer correctly with Veronica's prompts? Plain facts, answers from
documents fenced exactly as a chat turn fences them, answers from the memory block
she renders, and following an instruction's form.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.services.persona.defaults import DEFAULT_PERSONALITY, build_prompt
from eval.common import ratio, read_jsonl, says, words
from eval.material import documents_block, memory_block
from eval.turn import run_turn

CASES = Path(__file__).with_name("cases.jsonl")
KINDS = ("fact", "grounded", "memory", "instruction")

RULES = """How an answer is scored:
- right when it says every `must` word and one of each `any_of` group, and none of
  `forbid`;
- a grounded answer must also cite each document in `cite`, and no document that
  doesn't exist;
- a question the documents can't answer must be declined, and state no number the
  documents don't hold; it may cite the documents it checked. Declined means a sentence
  that names what was asked (the case's `subject`) says it isn't there, or the first
  sentence says so and gives no number, or the answer gives no number and says so
  anywhere: "Don't be late!" after the flight out's time given as the flight home's
  declines nothing;
- a form check is exact: one sentence, three bullets, only the number."""

# Every marker counts, back-to-back ones included: an answer is not code, so there is
# no index like items[0] to protect (the product's own pattern skips a marker that
# directly follows another).
_CITATION = re.compile(r"\[(\d+)\]")
# Words that decline; a declined answer needs one of them and no citation.
_DECLINE = words("not no don't doesn't isn't can't cannot unable unknown nothing mention")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_NUMBER = re.compile(r"\d+(?:[:.,]\d+)*")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    cases = read_jsonl(path)
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    for case in cases:
        if case["kind"] not in KINDS:
            raise ValueError(f"{case['id']}: unknown kind {case['kind']!r}")
        if case.get("no_answer") and not case.get("subject"):
            raise ValueError(f"{case['id']}: a question with no answer names its subject")
    return cases


def messages_for(case: dict[str, Any]) -> list[dict[str, str]]:
    """The system prompt a text turn would send for this case, then the question."""
    system = build_prompt(DEFAULT_PERSONALITY, spoken=False, tools_enabled=False)
    if case["kind"] == "grounded":
        documents = [(title, text) for title, text in case["documents"]]
        system = f"{system}\n\n{documents_block(documents)}"
    elif case["kind"] == "memory":
        block = memory_block(case.get("recalled", []), case.get("profile", {}))
        if block:
            system = f"{system}\n\n{block}"
    return [{"role": "system", "content": system}, {"role": "user", "content": case["question"]}]


def _form_ok(check: dict[str, Any], reply: str) -> bool:
    text = reply.strip()
    lines = [line for line in text.splitlines() if line.strip()]
    if "sentences" in check:
        sentences = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        if len(sentences) != check["sentences"]:
            return False
    if "bullets" in check:
        bullets = [line for line in lines if _BULLET.match(line)]
        if len(bullets) != check["bullets"] or len(bullets) != len(lines):
            return False
    if "only" in check and text.strip(" .*`") != check["only"]:
        return False
    if "starts" in check and (
        not text or text.split()[0].strip(".,!").casefold() not in check["starts"]
    ):
        return False
    if "max_words" in check and len(text.split()) > check["max_words"]:
        return False
    return "must" not in check or says(text, check["must"])


def _declined(reply: str, subject: list[str]) -> bool:
    """Whether a sentence about what was asked says it isn't there; or the first says so
    and gives no number ("I don't know."); or the answer gives no number at all and says
    so anywhere."""
    about = words(" ".join(subject))
    sentences = [part for part in _SENTENCE_END.split(reply) if part.strip()]
    said = [words(part) for part in sentences]
    if any(each & _DECLINE and each & about for each in said):
        return True
    if said and said[0] & _DECLINE and not _numbers(sentences[0]):
        return True
    return not _numbers(reply) and any(each & _DECLINE for each in said)


def _numbers(text: str) -> set[str]:
    """Every number in `text` written one way: "07:15" is "7:15", "2,150" is "2150".

    Citation markers and list numbering are not numbers the answer states.
    """
    text = _LIST_MARKER.sub(" ", _CITATION.sub(" ", text))
    found: set[str] = set()
    for raw in _NUMBER.findall(text):
        if _THOUSANDS.fullmatch(raw):
            raw = raw.replace(",", "")
        found.add(":".join(str(int(p)) if p.isdigit() else p for p in raw.split(":")))
    return found


def is_right(case: dict[str, Any], reply: str) -> bool:
    if case["kind"] == "instruction":
        return _form_ok(case["check"], reply)
    cited = {int(number) for number in _CITATION.findall(reply)}
    valid = set(range(1, len(case.get("documents", [])) + 1))
    if case.get("no_answer"):
        held = _numbers(" ".join([case["question"], *(text for _, text in case["documents"])]))
        return _declined(reply, case["subject"]) and cited <= valid and _numbers(reply) <= held
    if not says(reply, case.get("must", []), case.get("any_of", [])):
        return False
    if words(reply) & words(" ".join(case.get("forbid", []))):
        return False
    if "max_name" in case:
        mentions = re.findall(rf"\b{re.escape(case['name'])}\b", reply, re.IGNORECASE)
        if len(mentions) > case["max_name"]:
            return False
    if case["kind"] == "grounded":
        return set(case.get("cite", [])) <= cited and cited <= valid
    return True


async def run_case(llm: OllamaLLMClient, model: str, case: dict[str, Any]) -> dict[str, Any]:
    turn = await run_turn(llm, messages_for(case), model=model)
    return {
        "id": case["id"],
        "kind": case["kind"],
        "right": is_right(case, turn.text),
        "reply": turn.text.strip()[:400],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "overall": ratio(sum(1 for r in results if r["right"]), len(results)),
    }
    for kind in KINDS:
        of_kind = [r for r in results if r["kind"] == kind]
        summary[kind] = ratio(sum(1 for r in of_kind if r["right"]), len(of_kind))
    summary["cases"] = results
    return summary
