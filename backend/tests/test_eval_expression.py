"""The expression evaluation reads a reply's sounds as her voice would, and the call
lanes of the other evaluations ask on the prompt a call starts out with."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.adapters.llm.types import LlmChunk
from app.core.config import get_settings
from app.services.persona.defaults import EXPRESSION_RULES
from eval.expression import sounds
from eval.injection import attacks
from eval.tools import gate
from scripts import model_eval


class _Saying:
    """Answers every request with `text`, and keeps each request's system prompt."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.systems: list[str] = []

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        *,
        model: Any = None,
        keep_alive: Any = None,
        temperature: Any = None,
    ) -> AsyncIterator[LlmChunk]:
        self.systems.append(str(messages[0]["content"]))
        yield LlmChunk(content=self.text)
        yield LlmChunk(done=True)


def _read(kind: str, text: str) -> sounds.Reply:
    return sounds.read_sounds(sounds.Reply("x", kind, text, []))


def test_a_reply_s_sounds_are_what_her_voice_is_sent() -> None:
    reply = _read("funny", "[laugh] Good one. *giggles* [smiles] [Laugh] [gasp] See [1].")
    assert reply.tags == ["laugh", "chuckle", "laugh"]  # a direction naming a sound is one
    assert reply.improvised == ["*giggles*", "[smiles]", "[Laugh]", "[gasp]"]


def test_a_laugh_her_voice_would_drop_is_not_counted_as_voiced() -> None:
    assert _read("letdown", "[laugh] Oh no, that's rough.").voiced == []
    assert _read("funny", "[laugh] Good one. [sigh] Anyway.").voiced == ["laugh", "sigh"]
    # As a call chunks it: the clause before the sympathy, and a laugh left at the end.
    assert _read("letdown", "[laugh] You poor thing, that's the worst.").voiced == []
    assert _read("letdown", "Oh no, that's awful. [laugh]").voiced == []
    assert _read("letdown", "Oh no, that's awful. [laugh]").tags == ["laugh"]


def test_the_scores_count_what_they_say() -> None:
    replies = [
        _read("funny", "[laugh] Good one."),  # fitting
        _read("funny", "[sigh] Oh dear."),  # misplaced
        _read("letdown", "[sigh] What a pain."),  # fitting
        _read("letdown", "[chuckle] Classic bad timing."),  # mocking, voiced
        _read("serious", "[laugh] I'm so sorry."),  # mocking, not voiced
        _read("plain", "Paris. [laugh] [laugh]"),  # plain_tagged, over
        _read("plain", "*nods* Canberra."),  # improvised
        sounds.Reply("e", "plain", "", ["memory_save"]),  # empty
    ]
    summary = sounds.summarize(replies)  # ratios are kept to four places
    assert summary["fitting"] == round(2 / 4, 4)
    assert summary["mocking"] == round(2 / 3, 4)
    assert summary["mocking_voiced"] == round(1 / 3, 4)
    assert summary["misplaced"] == round(3 / 5, 4)
    assert summary["plain_tagged"] == round(1 / 2, 4)
    assert summary["over"] == round(1 / 7, 4)
    assert summary["improvised"] == round(1 / 7, 4)
    assert summary["empty"] == round(1 / 8, 4)


def test_the_cases_load_and_cover_every_kind() -> None:
    cases = sounds.load_cases()
    assert {case["kind"] for case in cases} == set(sounds.KINDS)


def test_a_case_of_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "cases.jsonl"
    bad.write_text('{"id": "x", "kind": "sad", "utterance": "Hi"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown kind"):
        sounds.load_cases(bad)


async def test_both_lanes_run_each_case_side_by_side() -> None:
    llm = _Saying("[laugh] Ha.")

    summary, _cases = await model_eval.run_family("expression", llm, "m", get_settings(), 2)  # type: ignore[arg-type]

    told = [EXPRESSION_RULES in system for system in llm.systems]
    assert told == [False, True, False, True]  # off, on, interleaved
    assert summary["on_tagged"] == 1.0 and summary["off_tagged"] == 1.0


def test_a_calls_lanes_ask_on_the_prompt_a_call_starts_with(tmp_path: Path) -> None:
    chat, _ = gate.tool_surface(get_settings(), tmp_path)
    call, _ = gate.tool_surface(get_settings(), tmp_path, spoken=True)
    bare, _ = gate.tool_surface(get_settings(), tmp_path, spoken=True, expressive=False)
    assert EXPRESSION_RULES in call
    assert EXPRESSION_RULES not in chat and EXPRESSION_RULES not in bare
    assert EXPRESSION_RULES in attacks.surface(get_settings(), tmp_path, spoken=True).system
    assert EXPRESSION_RULES not in attacks.surface(get_settings(), tmp_path).system
