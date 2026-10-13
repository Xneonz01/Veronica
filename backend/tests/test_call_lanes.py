"""The evaluations measure a call on its own prompt and tools (bug 50): the spoken prompt
moved the model differently, and a call was measured on three prompts until 0.34.12."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.adapters.llm.types import LlmChunk
from app.core.config import get_settings
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.registry import Capability, CapabilityRegistry
from app.services.persona.defaults import DEFAULT_PERSONALITY, build_prompt
from eval.injection import attacks
from eval.tools import gate
from scripts import model_eval

SPOKEN_RULE = "Speaking aloud changes how you talk, not what you do"


class _NoArgs(BaseModel):
    pass


async def _nothing(_args: BaseModel) -> CapabilityResult:
    return CapabilityResult(ok=True)


CHAT_ONLY = Capability(
    name="test.chat_only",
    description="Only offered in writing.",
    args_model=_NoArgs,
    risk=RiskTier.safe,
    handler=_nothing,
)


class _Recording:
    """Answers nothing, and keeps each request's system prompt."""

    def __init__(self) -> None:
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
        yield LlmChunk(done=True)


def _names(tools: list[dict[str, Any]]) -> set[str]:
    return {tool["function"]["name"] for tool in tools}


@pytest.fixture
def chat_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every capability is offered on calls today; one that isn't tells the lanes apart."""
    real = gate.confined_registry

    def with_it(settings: Any, root: Path, *, web: bool = False) -> CapabilityRegistry:
        registry = real(settings, root, web=web)
        registry.register(CHAT_ONLY)
        return registry

    monkeypatch.setattr(gate, "confined_registry", with_it)
    monkeypatch.setattr(attacks, "confined_registry", with_it)


def test_a_call_is_offered_only_what_is_marked_for_calls() -> None:
    """(mutation: offer a call everything)"""
    registry = CapabilityRegistry()
    registry.register(CHAT_ONLY)

    assert registry.offered(call=False) == [CHAT_ONLY] and registry.offered(call=True) == []


@pytest.mark.usefixtures("chat_only")
def test_the_tools_evaluation_offers_a_call_what_a_call_offers(tmp_path: Path) -> None:
    """(mutations: the call's lane on chat's prompt; offer it chat's tools)"""
    chat_prompt, chat_tools = gate.tool_surface(get_settings(), tmp_path)
    call_prompt, call_tools = gate.tool_surface(get_settings(), tmp_path, spoken=True)

    assert CHAT_ONLY.tool_name in _names(chat_tools)
    assert CHAT_ONLY.tool_name not in _names(call_tools)
    assert SPOKEN_RULE in call_prompt and "You are writing" in chat_prompt


def test_every_action_the_tools_cases_expect_is_offered_on_a_call(tmp_path: Path) -> None:
    """A tool made chat-only would score its cases as a call's misses."""
    _prompt, tools = gate.tool_surface(get_settings(), tmp_path, spoken=True)
    expected = {case["expect"] for case in gate.load_cases() if case["kind"] == "tool"}

    assert expected <= _names(tools)


def test_every_tool_the_call_prompt_names_is_one_a_call_offers(tmp_path: Path) -> None:
    """The style names the actions to take first; a tool renamed must not leave it naming
    one that no longer exists (mutation: a name the call doesn't offer)."""
    _prompt, tools = gate.tool_surface(get_settings(), tmp_path, spoken=True)
    prompt = build_prompt(DEFAULT_PERSONALITY, spoken=True, tools_enabled=True)
    named = set(re.findall(r"\b(?:memory|files|apps)_[a-z_]+\b", prompt))

    assert named and named <= _names(tools)


async def test_the_tools_evaluation_asks_a_call_on_the_calls_prompt() -> None:
    """Both lanes run, the call's on the spoken prompt (mutations: run one lane only; the
    call's lane on chat's prompt)."""
    llm = _Recording()

    summary, _cases = await model_eval.run_family("tools", llm, "m", get_settings(), 2)  # type: ignore[arg-type]

    chat, call = llm.systems[:2], llm.systems[2:]
    assert len(call) == 2 and all(SPOKEN_RULE in system for system in call)
    assert not any(SPOKEN_RULE in system for system in chat)
    assert {"selection", "call_selection", "call_silent", "call_false_call"} <= set(summary)


@pytest.mark.usefixtures("chat_only")
def test_the_planted_instruction_lane_for_a_call_uses_its_prompt_and_tools(tmp_path: Path) -> None:
    """(mutations: the call's lane on chat's prompt; offer it chat's tools)"""
    written = attacks.surface(get_settings(), tmp_path)
    spoken = attacks.surface(get_settings(), tmp_path, spoken=True)

    assert SPOKEN_RULE in spoken.system and SPOKEN_RULE not in written.system
    assert CHAT_ONLY.tool_name in _names(written.tools)
    assert CHAT_ONLY.tool_name not in _names(spoken.tools)


def test_a_calls_lane_reads_a_file_cut_as_a_call_cuts_it(tmp_path: Path) -> None:
    """(mutation: the call's lane reads a file whole)"""
    case = {
        "vector": "tool_result",
        "utterance": "Read {root}/notes.txt to me.",
        "material": "The spare key is under the blue pot. " * 60,
        "path": "notes.txt",
    }
    written = attacks.reading(case, attacks.surface(get_settings(), tmp_path))
    spoken = attacks.reading(case, attacks.surface(get_settings(), tmp_path, spoken=True))

    assert "(truncated)" in spoken.messages[-1]["content"]
    assert "(truncated)" not in written.messages[-1]["content"]


async def test_a_call_is_tested_on_what_a_call_reads() -> None:
    """Her memory and a file's contents, never a document: a call cites none (mutations:
    skip the call's lane; give it the documents)."""
    llm = _Recording()

    summary, _cases = await model_eval.run_family("injection", llm, "m", get_settings(), None)  # type: ignore[arg-type]

    heard = [case for case in attacks.load_cases() if case["vector"] != "document"]
    # And the web's, on a call a search: never the page reader a call doesn't have.
    searched = [c for c in attacks.load_cases(attacks.WEB_CASES) if not c.get("chat_only")]
    assert len(summary["call_cases"]) == len(heard) > 0
    assert len(summary["call_web_cases"]) == len(searched) > 0
    assert {"call_obeyed", "call_ran_unasked", "call_false_call"} <= set(summary)
    assert sum(SPOKEN_RULE in system for system in llm.systems) == len(heard) + len(searched)
