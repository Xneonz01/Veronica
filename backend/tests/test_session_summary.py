"""The recap written at a call's end (`voice/realtime/session_tasks.py`): best effort,
never in the way of hanging up, and quiet about a model that is away or not installed,
which is expected while one is being pulled."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.services.voice.realtime.session_tasks import _SUMMARY_EXAMPLE, summarize_session
from tests.fakes import FakeLLM, build_fake_memory_runtime

HISTORY = [
    {"role": "user", "content": "Remind me to water the ferns."},
    {"role": "assistant", "content": "I will."},
]


@pytest.mark.parametrize("llm", [FakeLLM([], unreachable=True), FakeLLM([], missing_model="m")])
async def test_a_model_away_or_not_installed_is_one_line_not_a_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, llm: FakeLLM
) -> None:
    """Every call's end logged it as an error with its traceback, into the black box
    (mutation: let the catch-all take it)."""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    session = SimpleNamespace(
        memory=memory, runtime=SimpleNamespace(llm=llm), settings=get_settings()
    )

    with caplog.at_level(logging.WARNING):
        await summarize_session(session, HISTORY)  # type: ignore[arg-type]

    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert "session summary skipped" in caplog.text
    await memory.store.close()


async def test_a_recap_is_kept_as_a_summary(tmp_path: Path) -> None:
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    llm = FakeLLM(["You asked to be reminded about the ferns."])
    session = SimpleNamespace(
        memory=memory, runtime=SimpleNamespace(llm=llm), settings=get_settings()
    )

    await summarize_session(session, HISTORY)  # type: ignore[arg-type]

    kept = [m.content for m in await memory.store.list_memories(include_archived=False)]
    assert kept == ["You asked to be reminded about the ferns."]
    await memory.store.close()


FERRIES = [
    {"role": "user", "content": "Can you compare the ferry timetables for the islands?"},
    {"role": "assistant", "content": "The morning boats are quicker."},
]


@pytest.mark.parametrize(
    ("history", "summary", "kept"),
    [
        (HISTORY, _SUMMARY_EXAMPLE, False),  # the instruction's example, for a fern call
        (HISTORY, "we were talking about keeping ferns watered", True),
        # A call about exactly that: the user's own words (mutation: judge the recap
        # alone, not by what was said).
        (FERRIES, _SUMMARY_EXAMPLE, True),
    ],
)
async def test_the_instructions_example_is_never_the_recap(
    tmp_path: Path, history: list[dict[str, str]], summary: str, kept: bool
) -> None:
    """Said at the next call as "Last time, ...": a model handed back the instruction's
    example for a call that never touched it (mutation: store any summary)."""
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    session = SimpleNamespace(
        memory=memory, runtime=SimpleNamespace(llm=FakeLLM([summary])), settings=get_settings()
    )

    await summarize_session(session, history)  # type: ignore[arg-type]

    assert (await memory.service.latest_summary() == summary) is kept
    await memory.store.close()
