"""What the extraction prompt's own examples may become (`extraction.unprompted`): a
whole copy of one is dropped when the user said at most half of its telling words, and
anything else is left alone. A model copied one of the instruction's old examples
out of the prompt into what it learned from a chat about something else; the next
examples were "User prefers short answers", which a model also wrote for a user who
had really asked to keep it brief."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.event_bus import InProcessEventBus
from app.services.memory import extraction
from app.services.memory import learning as learning_module
from app.services.memory.extraction import EXAMPLE_FACT, EXAMPLE_PREFERENCE, EXAMPLES
from app.services.voice.realtime import session_tasks
from tests.fakes import FakeLLM, build_fake_memory_runtime
from tests.test_memory_learning import learn


def said(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


def _extraction_llm(content: str) -> FakeLLM:
    """A fake model whose one reply is an extraction holding `content`."""
    fact = {"content": content, "confidence": 0.95}
    return FakeLLM([json.dumps({"profile": {}, "facts": [fact]})])


async def _learned(tmp_path: Path, history: list[dict[str, str]], content: str) -> bool:
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await learn(mem.learning, history, _extraction_llm(content), "fake-model")
    kept = any(m.content == content for m in await mem.service.list())
    await mem.store.close()
    return kept


@pytest.mark.parametrize(
    ("heard", "content", "kept"),
    [
        # A whole copy, from a chat about something else.
        ("What time is it in Lisbon?", EXAMPLE_FACT, False),
        ("What time is it in Lisbon?", EXAMPLE_PREFERENCE, False),
        ("What time is it in Lisbon?", "User prefers crossword puzzles printed on paper.", False),
        # One of its words said is not the example said (mutation: any word disarms it).
        ("I went sailing once, years ago.", EXAMPLE_FACT, False),
        # Said, it is theirs: most of its telling words are in what they said.
        ("I'm restoring an old sailing boat this summer.", EXAMPLE_FACT, True),
        ("I only do crossword puzzles on paper, printed out.", EXAMPLE_PREFERENCE, True),
        # The preference a person states most, in the words a model files it with
        # (mutation: the old example).
        ("Keep it brief, please.", "User prefers short answers", True),
        # More than an example, or part of one, is never taken for a copy (mutations:
        # judge by the example's words being in the fact; judge by any overlap).
        ("What time is it in Lisbon?", "User is restoring a wooden sailing boat on weekends", True),
        ("We sail on Sundays.", "User owns a boat", True),
    ],
)
async def test_a_whole_copy_of_an_example_is_dropped_and_nothing_else(
    tmp_path: Path, heard: str, content: str, kept: bool
) -> None:
    assert await _learned(tmp_path, said(heard), content) is kept


async def test_her_own_words_are_not_what_the_user_said(tmp_path: Path) -> None:
    """The example in her reply doesn't make it theirs (mutation: count every turn)."""
    history = [
        {"role": "user", "content": "What should I do this weekend?"},
        {"role": "assistant", "content": "Maybe start restoring a wooden sailing boat?"},
    ]

    assert await _learned(tmp_path, history, EXAMPLE_FACT) is False


async def test_a_dropped_copy_is_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Silent before: nothing said a fact had been left out (mutation: don't record)."""
    events: list[tuple[str, dict[str, object]]] = []

    class _Box:
        def record(self, _level: str, event: str, **fields: object) -> None:
            events.append((event, fields))

    monkeypatch.setattr(learning_module, "current_blackbox", lambda: _Box())

    await _learned(tmp_path, said("What time is it in Lisbon?"), EXAMPLE_FACT)

    assert ("learning.example_dropped", {"count": 1}) in events


def test_every_example_is_told_apart_by_several_words() -> None:
    """A copy is recognised by its telling words: an example with one or two of them
    would be every other fact's (mutation: a telling word added to the common ones)."""
    for example in (*EXAMPLES, session_tasks._SUMMARY_EXAMPLE):
        telling = extraction._telling(example)
        assert len(telling) >= 3, example
        assert not telling & extraction._COMMON
