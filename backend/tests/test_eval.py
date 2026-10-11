"""The model evaluations' own rules, checked without a model.

A metric nobody can test without a GPU is a metric nobody tests, and these are the
numbers a model change is decided on.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.types import LlmChunk
from app.core.config import get_settings
from app.services.chat.context import document_fence, documents_header
from app.services.memory.extraction import Filing, filing_for, parse_extraction
from app.services.memory.models import ExtractedFact, Extraction, LearningMode, MemoryKind
from app.services.tools import claims
from eval.accuracy import answers
from eval.common import median_of, percentile, ratio, result_path, says, words, write_result
from eval.memory import extraction
from eval.tools import gate
from eval.turn import call_arguments, run_turn
from scripts import model_eval


class FakeLlm:
    """Streams a fixed reply, as the Ollama adapter would."""

    def __init__(self, text: str = "", calls: list[dict[str, Any]] | None = None) -> None:
        self.text = text
        self.calls = calls

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        *,
        model: str | None = None,
        keep_alive: str | None = None,
        temperature: Any = None,
    ) -> AsyncIterator[LlmChunk]:
        if self.text:
            yield LlmChunk(content=self.text)
        yield LlmChunk(tool_calls=self.calls, done=True)


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": arguments}}


# ---------------------------------------------------------------- matching and numbers


def test_words_fold_plurals_numbers_possessives_and_filler() -> None:
    assert words("User works nights on Tuesdays") == {"work", "night", "tuesday"}
    assert words("Rowan's sister has three cats") == {"rowan", "sister", "3", "cat"}
    assert words("a part-time job") == {"part", "time", "job"}


def test_symbols_split_words_but_times_and_prices_stay_whole() -> None:
    assert words("Water boils at 100°C.") == {"water", "boil", "100", "c"}
    assert words("Departs 07:15; costs £2,150.") == {"depart", "07:15", "cost", "2,150"}
    assert words("I don't know.") == {"i", "don't", "know"}


def test_a_claim_needs_every_must_word_and_one_of_each_group() -> None:
    assert says("User dislikes horror films", ["horror"], [["dislike", "not"]])
    assert not says("User likes horror films", ["horror"], [["dislike", "not"]])
    assert not says("User likes films", ["horror"])


def test_a_claim_can_rule_words_out() -> None:
    assert says("User has a dog named Biscuit", ["biscuit"], none_of=["future", "would"])
    assert not says("User would name a future dog Biscuit", ["biscuit"], none_of=["future"])


def test_nothing_to_divide_is_no_number() -> None:
    assert ratio(0, 0) is None
    assert ratio(1, 3) == 0.3333
    assert percentile([], 0.9) is None
    assert percentile([10.0, 20.0, 30.0, 40.0], 0.9) == 40.0
    assert median_of([{"x": 1.0}, {"x": None}, {"x": 3.0}], "x") == 2.0
    assert median_of([{"x": None}], "x") is None


def test_a_result_is_never_overwritten(tmp_path: Path) -> None:
    first = result_path("0.1.0", "tools", "qwen3.5:9b", tmp_path)
    assert first.name == "0.1.0_tools_qwen3_5_9b_r1.json"
    write_result(first, {"n": 1})
    second = result_path("0.1.0", "tools", "qwen3.5:9b", tmp_path)
    assert second.name.endswith("_r2.json")
    with pytest.raises(FileExistsError):
        write_result(first, {"n": 2})


def test_arguments_sent_as_a_json_string_are_read() -> None:
    assert call_arguments({"function": {"arguments": '{"path": "a"}'}}) == {"path": "a"}
    assert call_arguments({"function": {"arguments": "not json"}}) == {}


async def test_a_turn_is_timed_and_its_calls_named() -> None:
    turn = await run_turn(FakeLlm("", [call("files_read", path="x")]), [], model="m")  # type: ignore[arg-type]
    assert turn.names == ["files_read"]
    assert turn.first_ms is not None and turn.first_ms <= turn.total_ms


# ---------------------------------------------------------------- tools


def test_every_expected_tool_is_one_the_product_offers(tmp_path: Path) -> None:
    _, tools = gate.tool_surface(get_settings(), tmp_path)
    offered = {tool["function"]["name"] for tool in tools}
    expected = {case["expect"] for case in gate.load_cases() if case["kind"] == "tool"}
    assert expected <= offered
    assert {"tool", "no_tool"} == {case["kind"] for case in gate.load_cases()}


def test_arguments_are_judged_as_the_product_would(tmp_path: Path) -> None:
    gate.build_tree(tmp_path)
    roots = [tmp_path.resolve()]
    rule = {"path": {"is": "notes/shopping.md"}}
    assert gate.check_arguments(rule, {"path": str(tmp_path / "notes/shopping.md")}, roots)
    # Read inside her folder, as the product has since 0.34.1 (bug 29); never above it.
    assert gate.check_arguments(rule, {"path": "notes/shopping.md"}, roots)
    assert not gate.check_arguments(rule, {"path": "../notes/shopping.md"}, roots)
    assert not gate.check_arguments(rule, {"path": str(tmp_path / "notes/todo.md")}, roots)
    assert gate.check_arguments({"content": {"must": ["tea"]}}, {"content": "Likes tea"}, roots)
    assert gate.check_arguments({"name": {"one_of": ["notepad"]}}, {"name": "Notepad"}, roots)
    assert not gate.check_arguments({"query": {}}, {}, roots)


async def test_a_run_hides_the_tree_and_scores_the_first_call(tmp_path: Path) -> None:
    gate.build_tree(tmp_path)
    root = tmp_path.resolve()
    case = {
        "id": "t",
        "kind": "tool",
        "expect": "files_read",
        "utterance": "Read {root}/notes/todo.md",
        "args": {"path": {"is": "notes/todo.md"}},
    }
    llm = FakeLlm("", [call("files_read", path=f"{root.as_posix()}/notes/todo.md")])
    outcome = await gate.run_case(llm, "m", case, ("system", []), root)  # type: ignore[arg-type]
    assert outcome.right and outcome.args_ok
    assert outcome.args_seen == {"path": "{root}/notes/todo.md"}


async def test_a_tool_case_after_an_unbacked_claim_sends_the_caveat_as_chat_keeps_it(
    tmp_path: Path,
) -> None:
    """The turns before its message, a reply's caveats after it (bug 37; mutation: send
    the reply alone)."""
    root = tmp_path.resolve()
    history = [
        {"role": "user", "content": "Write it to {root}/notes/list.md."},
        {"role": "assistant", "content": "I've written it.", "caveats": ["files"]},
    ]
    case = {"id": "t", "kind": "tool", "expect": "files_write", "history": history,
            "utterance": "Yes, please do."}  # fmt: skip
    seen: list[list[dict[str, Any]]] = []
    llm = FakeLlm("", [call("files_write", path="notes/list.md", content="eggs")])
    original = llm.stream_chat

    def recording(messages: list[dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
        seen.append(messages)
        return original(messages, *args, **kwargs)

    llm.stream_chat = recording  # type: ignore[method-assign]
    await gate.run_case(llm, "m", case, ("system", []), root)  # type: ignore[arg-type]

    assert [m["content"] for m in seen[0][1:]] == [
        f"Write it to {root.as_posix()}/notes/list.md.",
        f"I've written it.\n\n{claims.CAVEAT['files']}",
        "Yes, please do.",
    ]


def test_the_caveats_a_tool_case_carries_are_the_ones_the_check_gives() -> None:
    """A case's history is what chat would hold, so its caveats are what the claim check
    says of that reply today (mutation: a case's family changed by hand)."""
    apps = get_settings().capabilities.app_allowlist
    for case in gate.load_cases():
        for turn in case.get("history", []):
            if turn["role"] == "assistant":
                text = turn["content"].replace("{root}", "C:/work")
                assert claims.unsupported(text, [], apps) == turn.get("caveats", []), case["id"]


def test_the_tool_summary_keeps_its_failures_apart() -> None:
    outcomes = [
        gate.CaseOutcome("t1", "tool", "memory_save", ["memory_save"], 100.0, args_ok=True),
        gate.CaseOutcome("t2", "tool", "memory_save", ["memory_recall"], 200.0),
        gate.CaseOutcome("t3", "tool", "files_read", [], None),
        gate.CaseOutcome("n1", "no_tool", None, ["memory_recall"], 50.0),
        gate.CaseOutcome("n2", "no_tool", None, ["memory_save"], 60.0),
    ]
    cases = [{"id": o.id} for o in outcomes]
    cases[3]["acceptable"] = ["memory_recall"]
    summary = gate.summarize(outcomes, cases)
    assert summary["selection"] == 0.3333
    assert summary["wrong_tool"] == 0.3333 and summary["silent"] == 0.3333
    assert summary["false_call"] == 0.5
    assert summary["args_valid"] == 1.0


# ---------------------------------------------------------------- memory


def test_the_gold_set_loads_and_names_every_claim() -> None:
    assert len(extraction.load_gold()) >= 15


def test_traps_come_first_and_an_invention_counts_as_false() -> None:
    case = {
        "expected": [{"must": ["cello"]}],
        "traps": [{"must": ["professional"]}],
    }
    scored = extraction.score(
        case,
        [
            "User is a professional cellist learning the cello",
            "User is learning the cello",
            "User plays the cello",
            "User loves Mondays",
        ],
    )
    assert scored.met == 1 and scored.extra == 1
    assert scored.false_memories == [
        "User is a professional cellist learning the cello",
        "User loves Mondays",
    ]


def test_an_allowed_claim_is_neither_required_nor_false() -> None:
    case = {
        "expected": [{"must": ["raised", "bed"]}],
        "traps": [],
        "allowed": [{"must": ["pallet"]}],
    }
    scored = extraction.score(case, ["User is repurposing old pallets"])
    assert scored.met == 0 and scored.allowed == 1 and scored.false_memories == []
    summary = extraction.summarize(
        [{"id": "m", "parsed": True, "proposed": asdict(scored), "filed": asdict(scored)}]
    )
    assert summary["filed_precision"] == 1.0 and summary["filed_recall"] == 0.0


def test_only_what_proactive_learning_would_save_is_filed() -> None:
    found = Extraction(
        profile={"nickname": "Ro", "name": "Veronica"},
        facts=[
            ExtractedFact(content="User is vegetarian", confidence=0.9),
            ExtractedFact(content="User might like jazz", confidence=0.4),
            ExtractedFact(content="User's bank PIN is 4821", confidence=0.95),
            ExtractedFact(content="Talked about lunch", confidence=0.9, kind=MemoryKind.episodic),
        ],
    )
    turns = [
        {"role": "assistant", "content": "Hi, I'm Veronica."},
        {"role": "user", "content": "Call me Ro."},
    ]
    assert extraction.filed_active(found, threshold=0.7, cap=8, turns=turns) == [
        "nickname: Ro",
        "User is vegetarian",
    ]
    assert len(extraction.proposed(found)) == 6


async def test_a_reply_that_is_not_json_loses_the_session() -> None:
    case = {"id": "m", "turns": [{"role": "user", "content": "hi"}], "expected": [], "traps": []}
    result = await extraction.run_case(FakeLlm("no json here"), "m", case, 0.7, 8)  # type: ignore[arg-type]
    assert result["parsed"] is False
    summary = extraction.summarize([result])
    assert summary["parse_failures"] == 1 and summary["filed_precision"] is None
    assert result["reply"] == "no json here"  # kept, so a failure can be read afterwards


async def test_a_memory_result_keeps_the_temperature_it_was_asked_at() -> None:
    """The same model at another temperature is another result (mutation: leave it
    out)."""
    settings = get_settings()
    llm = FakeLlm('{"profile": {}, "facts": []}')

    summary, _cases = await model_eval.run_family("memory", llm, "m", settings, 1)  # type: ignore[arg-type]

    assert summary["temperature"] == settings.memory.learning_temperature == 0.2


# ---------------------------------------------------------------- accuracy


def test_every_accuracy_kind_is_present() -> None:
    assert {case["kind"] for case in answers.load_cases()} == set(answers.KINDS)


def test_a_grounded_answer_must_cite_its_documents() -> None:
    case = {
        "kind": "grounded",
        "documents": [["a", "x"], ["b", "y"]],
        "must": ["sink"],
        "cite": [2],
    }
    assert answers.is_right(case, "Under the kitchen sink [2].")
    assert not answers.is_right(case, "Under the kitchen sink.")
    assert not answers.is_right(case, "Under the kitchen sink [2][3].")


def test_an_unanswerable_question_is_declined_without_inventing_an_answer() -> None:
    case = {
        "kind": "grounded",
        "documents": [["trip.md", "Flight departs at 07:15. Check-in from 15:00."]],
        "question": "How much did the hotel cost?",
        "no_answer": True,
        "subject": ["hotel"],
    }
    assert answers.is_right(case, "The documents don't say what it cost.")
    # Citing what it checked, and repeating what the document holds, is still a decline.
    assert answers.is_right(case, "I don't know. The notes only give the 7:15 flight [1].")
    assert not answers.is_right(case, "It was 150 euros [1].")
    assert not answers.is_right(case, "The notes don't say, but likely 150 euros.")
    assert not answers.is_right(case, "It isn't in the notes [2].")  # no such document


def test_a_near_miss_given_as_the_answer_is_wrong() -> None:
    """The flight out for the flight home (bug 41): a time the document holds, stated as
    the answer, is no decline, whatever else the answer says (mutations: any decline word
    anywhere; drop the first sentence's rule)."""
    case = {
        "kind": "grounded",
        "documents": [["trip.md", "Flight to Lisbon departs Friday at 07:15."]],
        "question": "What time is our flight home from Lisbon?",
        "no_answer": True,
        "subject": ["home", "return"],
    }
    assert not answers.is_right(case, "Your flight leaves at 07:15 [1].")
    assert not answers.is_right(case, "Your flight leaves at 07:15 [1]. Don't be late!")
    assert not answers.is_right(case, "It leaves at 07:15 [1], and there's no need to hurry.")
    assert answers.is_right(case, "The notes only have the flight out, at 07:15 [1], not home.")
    assert answers.is_right(case, "I don't know. Only the flight out is there, at 07:15 [1].")


def test_a_question_with_no_answer_names_what_it_asks_about(tmp_path: Path) -> None:
    """Without a subject a decline can't be told from a stray negation (mutation: load it)."""
    path = tmp_path / "cases.jsonl"
    case = {"id": "x1", "kind": "grounded", "documents": [], "question": "Q?", "no_answer": True}
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="subject"):
        answers.load_cases(path)


def test_memory_answers_respect_forbidden_words_and_the_name_rule() -> None:
    veg = {"kind": "memory", "must": [], "forbid": ["chicken"]}
    assert answers.is_right(veg, "A chickpea curry.")
    assert not answers.is_right(veg, "Roast chicken.")
    never = {"kind": "memory", "must": [], "name": "rowan", "max_name": 0}
    assert not answers.is_right(never, "Sure, Rowan: take breaks.")


def test_form_checks_are_exact() -> None:
    one = {"kind": "instruction", "check": {"sentences": 1}}
    assert answers.is_right(one, "Plants turn light into sugar.")
    assert not answers.is_right(one, "Plants use light. They make sugar.")
    bullets = {"kind": "instruction", "check": {"bullets": 3}}
    assert answers.is_right(bullets, "- a\n- b\n- c")
    assert not answers.is_right(bullets, "Here you go:\n- a\n- b\n- c")
    assert answers.is_right({"kind": "instruction", "check": {"only": "144"}}, "144")
    assert answers.is_right({"kind": "instruction", "check": {"starts": ["yes"]}}, "Yes, it is.")


def test_the_prompt_carries_the_documents_and_the_memory_block_as_a_turn_would() -> None:
    grounded = answers.messages_for(
        {"kind": "grounded", "documents": [["notes.md", "Text"]], "question": "Q"}
    )
    assert "Relevant documents." in grounded[0]["content"]
    assert "<<doc 1 | notes.md" in grounded[0]["content"]
    memory = answers.messages_for(
        {
            "kind": "memory",
            "profile": {"name": "Rowan", "address_by_name": "never"},
            "recalled": [],
            "question": "Q",
        }  # fmt: skip
    )
    assert "Do not address the user by name." in memory[0]["content"]


# ---------------------------------------------------------------- the product's own rules


def test_the_filing_rule() -> None:
    proactive, review = LearningMode.proactive, LearningMode.review_only
    fact, episodic = MemoryKind.fact, MemoryKind.episodic
    assert filing_for("Don't use my name", 0.9, fact, proactive, 0.7) is Filing.address
    assert filing_for("Talked about lunch", 0.9, episodic, proactive, 0.7) is Filing.drop
    assert filing_for("Likes tea", 0.9, fact, proactive, 0.7) is Filing.store
    assert filing_for("Likes tea", 0.5, fact, proactive, 0.7) is Filing.queue
    assert filing_for("Likes tea", 0.9, fact, review, 0.7) is Filing.queue


def test_an_extraction_is_read_out_of_surrounding_prose() -> None:
    reply = 'Here it is: {"profile": {}, "facts": [{"content": "Likes tea"}]} Done.'
    assert parse_extraction(reply).facts[0].content == "Likes tea"
    fenced = '```json\n{"facts": [{"content": "Likes tea"}]}\n```'
    assert parse_extraction(fenced).facts[0].content == "Likes tea"


@pytest.mark.parametrize("reply", ["nothing", "{broken", '{"facts": [}', '{"facts": {"x": 1}}'])
def test_a_reply_with_no_extraction_in_it_is_a_failure_not_nothing_learned(reply: str) -> None:
    """Read as an empty extraction, it would pass for "nothing to learn" (mutation:
    return an empty one)."""
    with pytest.raises(ValueError):
        parse_extraction(reply)


def test_one_fact_it_cannot_read_leaves_the_rest() -> None:
    """Validated as a whole, one bad fact dropped everything learned in the session
    (bug 24; mutation: validate the facts together)."""
    reply = (
        '{"profile": {"city": "Leeds"}, "facts": [{"content": "Likes tea"}, '
        '{"content": "Swims", "kind": "skill"}, {"confidence": 0.9}, "Walks on Fridays"]}'
    )

    extraction = parse_extraction(reply)

    assert [fact.content for fact in extraction.facts] == ["Likes tea"]
    assert extraction.malformed == 3
    assert extraction.profile == {"city": "Leeds"}


def test_a_bare_list_is_read_as_the_facts() -> None:
    """Asked for nothing, qwen3.5:9b sometimes answers `[]`; a bare list of facts was
    lost with it (mutation: read objects only)."""
    assert parse_extraction("[]") == Extraction()
    assert [fact.content for fact in parse_extraction('[{"content": "Likes tea"}]').facts] == [
        "Likes tea"
    ]


def test_a_document_is_fenced_with_a_marker_only_this_turn_knows() -> None:
    assert "<<end doc n abc>>" in documents_header("abc")
    assert (
        document_fence(1, "a[1]<b>", "Text", "abc")
        == "<<doc 1 | a1b abc>>\nText\n<<end doc 1 abc>>"
    )


def test_a_case_can_carry_the_memories_a_turn_would_retrieve() -> None:
    prompt = gate.with_memory("You are Veronica.", ["User goes to the gym on Tuesdays."])

    assert prompt.startswith("You are Veronica.\n\n")
    assert "Recall — User goes to the gym on Tuesdays." in prompt
    assert gate.with_memory("You are Veronica.", []) == "You are Veronica."
