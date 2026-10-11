"""The voice tool loop: running capabilities mid-call.

A tool round must not leave dead air, the loop must always end in speech, and
barge-in must still interrupt a capability in flight and release the turn gate,
or the call stops hearing the user.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.tools import ToolBroker
from app.services.voice.realtime import tool_loop, workers
from app.services.voice.realtime.session import RealtimeSession
from app.services.voice.realtime.session_emit import State
from app.services.voice.realtime.voice_events import VoiceEvent
from app.services.voice.turn import TurnEvent
from tests.fakes import (
    FakeDetector,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    ScriptedLLM,
    build_fake_kernel_runtime,
    build_fake_memory_runtime,
    build_fake_runtime,
)


async def _session(
    tmp_path: Path,
    script: list[dict[str, Any]],
    *,
    roots: list[str] | None = None,
    trace: list[Any] | None = None,
) -> tuple[FakeWS, Any, RealtimeSession]:
    bus = InProcessEventBus()
    memory = await build_fake_memory_runtime(tmp_path / "db", bus)
    kernel = await build_fake_kernel_runtime(memory, bus, file_roots=roots or [str(tmp_path)])
    llm = ScriptedLLM(script)
    ws = FakeWS()
    broker = ToolBroker(
        kernel.kernel,
        kernel.registry,
        actor="veronica:voice",
        voice_only=True,
    )
    session = RealtimeSession(
        ws,  # type: ignore[arg-type]
        build_fake_runtime(stt=FakeSTT([], None), tts=FakeTTS(trace=trace), llm=llm),  # type: ignore[arg-type]
        bus,
        get_settings(),
        persona=lambda: "You are Veronica.",
        metrics_sink=ListMetricsSink(),
        detector=FakeDetector([]),
        memory=memory,
        tools=broker,
    )
    return ws, (memory, kernel), session


def _spoken(session: RealtimeSession) -> str:
    """Everything handed to TTS this turn.

    `_speak` puts synthesized PCM on `audio_out`; the loop that drains it to the socket
    only runs inside a real call, so read the queue directly. FakeTTS synthesizes a
    sentence to its own bytes, which makes the queue the transcript of what was said.
    """
    chunks: list[bytes] = []
    while not session.audio_out.empty():
        item = session.audio_out.get_nowait()
        if isinstance(item, bytes):  # not the marks that open each reply
            chunks.append(item)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _read_call(path: Path) -> dict[str, Any]:
    return {"tool_calls": [{"function": {"name": "files_read", "arguments": {"path": str(path)}}}]}


# -- exposure ------------------------------------------------------------------


async def test_the_voice_tool_list_is_the_exposed_capabilities(tmp_path: Path) -> None:
    """`voice_only` filters on `cap.voice_exposed`.

    Checks that the capabilities actually set the flag, not just that the filter exists.
    """
    _ws, _rt, session = await _session(tmp_path, [{"tokens": ["hi"]}])
    names = {schema["function"]["name"] for schema in session.tools.schemas()}  # type: ignore[union-attr]

    assert {"files_read", "files_list", "files_search", "files_write", "apps_open"} <= names
    assert {"memory_save", "memory_recall"} <= names  # memory is always available


# -- the loop ------------------------------------------------------------------


async def test_a_voice_turn_runs_a_capability_and_speaks_the_answer(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("the roof needs fixing", encoding="utf-8")
    ws, (memory, kernel), session = await _session(
        tmp_path, [_read_call(note), {"tokens": ["Your note says the roof needs fixing."]}]
    )

    await workers.respond(session, "what does my note say?")

    assert "roof needs fixing" in _spoken(session)
    assert session._state is State.listening
    rows = await kernel.audit.list()
    assert rows[0]["capability"] == "files.read"
    assert rows[0]["actor"] == "veronica:voice"  # distinguishable from the text path
    await memory.store.close()


async def test_nothing_is_spoken_for_a_round_that_turns_out_to_be_a_tool_round(
    tmp_path: Path,
) -> None:
    """A tool round speaks nothing of its own.

    In practice the model emits no prose in a tool round, and the loop must not add
    any, so nothing is said that a later round would supersede. No tool name or path
    is spoken either.
    """
    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    ws, (memory, _k), session = await _session(
        tmp_path, [_read_call(note), {"tokens": ["It says hello."]}]
    )

    await workers.respond(session, "read my note")

    spoken = _spoken(session)
    assert "It says hello." in spoken
    assert "files_read" not in spoken and str(note) not in spoken  # no tool name, no path
    await memory.store.close()


async def test_the_rounds_always_terminate_in_speech(tmp_path: Path) -> None:
    """A model that keeps asking for tools must still end the turn with an answer,
    never with silence — the final round is forced tool-less."""
    note = tmp_path / "note.txt"
    note.write_text("x", encoding="utf-8")
    ws, (memory, _k), session = await _session(
        tmp_path,
        [_read_call(note), _read_call(note), _read_call(note), {"tokens": ["Enough."]}],
    )

    await workers.respond(session, "keep reading it")

    assert session._state is State.listening
    assert session.turn_in_flight is False
    await memory.store.close()


def _slow_tools(session: RealtimeSession, trace: list[Any]) -> None:
    """Make every tool take long enough to need the filler, and mark when it is done."""
    original = session.tools.execute  # type: ignore[union-attr]

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(tool_loop.ACK_AFTER_S + 0.15)
        outcome = await original(*args, **kwargs)
        trace.append("tool_done")
        return outcome

    session.tools.execute = slow  # type: ignore[union-attr,method-assign,assignment]


async def test_a_slow_tool_gets_a_spoken_acknowledgement(tmp_path: Path) -> None:
    """A slow tool gets a short spoken acknowledgement, while it runs.

    A tool round says nothing on its own, so without the filler a slow capability
    would leave a silent gap that sounds like the call has broken. Said after the tool,
    joined to the answer ("One moment.It says hello."), it filled nothing.
    """
    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    trace: list[Any] = []
    ws, (memory, _k), session = await _session(
        tmp_path, [_read_call(note), {"tokens": ["It says hello."]}], trace=trace
    )
    _slow_tools(session, trace)

    await workers.respond(session, "read my note")

    assert trace[0] in [("synth", ack) for ack in tool_loop._ACKS]  # the filler varies
    assert trace[1:] == ["tool_done", ("synth", "It says hello.")]
    await memory.store.close()


async def test_what_she_says_with_a_tool_call_is_said_before_the_tool_runs(
    tmp_path: Path,
) -> None:
    """A model call's last sentence is spoken when the call ends, not with the next one.

    The chunker holds a sentence until text arrives after it, and nothing follows the
    last one: "Let me check." waited for the tool it announced, then was said with the
    answer.
    """
    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    trace: list[Any] = []
    with_prose = {"tokens": ["Let me", " check."], **_read_call(note)}
    ws, (memory, _k), session = await _session(
        tmp_path, [with_prose, {"tokens": ["It says hello."]}], trace=trace
    )
    _slow_tools(session, trace)

    await workers.respond(session, "read my note")

    # No filler either: she has already said something this round.
    assert trace == [("synth", "Let me check."), "tool_done", ("synth", "It says hello.")]
    await memory.store.close()


def _screen(session: RealtimeSession) -> list[str]:
    """What the call screen is sent of her reply, token by token; it joins them as is."""
    tokens: list[str] = []

    async def seen(event: Event) -> None:
        tokens.append(event.payload["text"])

    session._bus.subscribe(VoiceEvent.assistant_token, seen)
    return tokens


async def test_two_slow_tools_in_one_round_get_one_filler(tmp_path: Path) -> None:
    # Each call used to decide alone, so the second said "One moment." again.
    notes = [tmp_path / "a.txt", tmp_path / "b.txt"]
    for note in notes:
        note.write_text("hello", encoding="utf-8")
    both = {
        "tool_calls": [*_read_call(notes[0])["tool_calls"], *_read_call(notes[1])["tool_calls"]]
    }
    trace: list[Any] = []
    ws, (memory, _k), session = await _session(
        tmp_path, [both, {"tokens": ["Both say hello."]}], trace=trace
    )
    _slow_tools(session, trace)
    screen = _screen(session)

    await workers.respond(session, "read both notes")

    assert trace[1:] == ["tool_done", "tool_done", ("synth", "Both say hello.")]
    assert "".join(screen) == f"{trace[0][1]} Both say hello."  # a space between rounds
    await memory.store.close()


async def test_a_filler_that_fails_to_be_said_cancels_the_tool(tmp_path: Path) -> None:
    """A turn that has failed doesn't leave its capability running.

    Only a barge-in cancelled it: a synthesis error in the filler ended the turn with an
    error while the tool went on, and a write could land after the user was told the
    turn had failed.
    """
    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    trace: list[Any] = []
    ws, (memory, _k), session = await _session(
        tmp_path, [_read_call(note), {"tokens": ["It says hello."]}], trace=trace
    )
    _slow_tools(session, trace)

    class _Unspeakable(FakeTTS):
        def synthesize_stream(self, text: str, voice_id: str) -> Any:
            if text in tool_loop._ACKS:
                raise RuntimeError("synthesis failed")
            return super().synthesize_stream(text, voice_id)

    session.runtime.tts = _Unspeakable()  # type: ignore[assignment]
    await workers.respond(session, "read my note")
    await asyncio.sleep(0.4)  # longer than the tool had left to run

    assert "tool_done" not in trace
    assert session._state is State.listening
    await memory.store.close()


async def test_a_refused_tool_still_ends_in_a_spoken_reply(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    workspace = tmp_path / "work"
    workspace.mkdir()
    ws, (memory, _k), session = await _session(
        tmp_path,
        [_read_call(outside), {"tokens": ["I can't open that one."]}],
        roots=[str(workspace)],
    )

    await workers.respond(session, "read the secret file")

    spoken = _spoken(session)
    assert "can't open that one" in spoken
    assert "path_outside_roots" not in spoken  # never a code, aloud
    assert "secret" not in spoken  # and never the content it refused
    assert session._state is State.listening
    await memory.store.close()


def test_the_acknowledgement_is_varied_and_says_nothing_technical() -> None:
    from datetime import datetime

    picked = {
        tool_loop.acknowledgement(datetime(2026, 1, 1, 9, minute, second))
        for minute, second in [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)]
    }
    assert len(picked) > 1, "the filler never varies"
    for line in tool_loop._ACKS:
        assert len(line) < 40 and "_" not in line and "/" not in line


# -- barge-in ------------------------------------------------------------------


async def test_barge_in_during_a_tool_call_leaves_the_call_able_to_hear(
    tmp_path: Path,
) -> None:
    """A turn cancelled mid-flight releases `turn_in_flight`.

    Otherwise `ingest_loop` skips every later speech_start and the call stops hearing
    the user.
    """
    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    ws, (memory, _k), session = await _session(
        tmp_path, [_read_call(note), {"tokens": ["It says hello."]}]
    )

    started = asyncio.Event()
    release = asyncio.Event()
    original = session.tools.execute  # type: ignore[union-attr]

    async def hang(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await release.wait()  # cancelled here by the barge-in
        return await original(*args, **kwargs)

    session.tools.execute = hang  # type: ignore[union-attr,method-assign,assignment]
    session.turn_in_flight = True
    session.spawn_respond("read my note")
    await asyncio.wait_for(started.wait(), timeout=5)

    await session.trigger_barge_in(b"\x00\x00")

    assert session.turn_in_flight is False, "the gate stayed shut — the call is deaf"
    assert session.barge.armed is False
    assert session._state is State.listening
    # And the interrupting speech opened a fresh utterance, so the next words are heard.
    assert session.utterance_q is not None
    release.set()
    await memory.store.close()


async def test_a_cancelled_tool_round_speaks_nothing_afterwards(tmp_path: Path) -> None:
    """A capability that finishes after the barge-in must not reach the speaker: the
    call has moved on and its output belongs to a turn the user abandoned."""
    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    ws, (memory, _k), session = await _session(
        tmp_path, [_read_call(note), {"tokens": ["It says hello."]}]
    )

    started = asyncio.Event()
    original = session.tools.execute  # type: ignore[union-attr]

    async def slow(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await asyncio.sleep(5)
        return await original(*args, **kwargs)

    session.tools.execute = slow  # type: ignore[union-attr,method-assign,assignment]
    session.turn_in_flight = True
    session.spawn_respond("read my note")
    await asyncio.wait_for(started.wait(), timeout=5)
    await session.trigger_barge_in(b"\x00\x00")
    await asyncio.sleep(0.05)

    assert "It says hello." not in _spoken(session)
    await memory.store.close()


# -- sessions without tools ----------------------------------------------------


async def test_a_session_with_no_kernel_still_replies(tmp_path: Path) -> None:
    """With `tools=None` the session makes a single tool-less pass.

    That is the path when no kernel or memory runtime is wired, and in most tests.
    """
    bus = InProcessEventBus()
    ws = FakeWS()
    session = RealtimeSession(
        ws,  # type: ignore[arg-type]
        build_fake_runtime(  # type: ignore[arg-type]
            stt=FakeSTT([], None), tts=FakeTTS(), llm=ScriptedLLM([{"tokens": ["Just talking."]}])
        ),
        bus,
        get_settings(),
        persona=lambda: "You are Veronica.",
        metrics_sink=ListMetricsSink(),
        detector=FakeDetector([TurnEvent.speech_start]),
    )
    assert session.tools is None

    await workers.respond(session, "hello")

    assert "Just talking." in _spoken(session)
    assert session._state is State.listening


async def test_the_users_words_actually_reach_the_model(tmp_path: Path) -> None:
    """The user's turn reaches the model's prompt.

    `respond` builds its prompt from `history`, not from its own argument; `run_stt`
    appends the user turn beforehand. `ScriptedLLM` replies the same whatever it is
    sent, so the other tests would pass even with that wiring broken.
    """
    _ws, (memory, _k), session = await _session(tmp_path, [{"tokens": ["Sure."]}])
    session.history.append({"role": "user", "content": "what did I ask about the roof?"})

    await workers.respond(session, "what did I ask about the roof?")

    sent = session.runtime.llm.calls[0]  # type: ignore[union-attr]
    assert sent[0]["role"] == "system"
    assert any(m["role"] == "user" and "roof" in m["content"] for m in sent), (
        "the user's turn never reached the model"
    )
    await memory.store.close()


async def test_the_voice_prompt_tells_her_to_summarise_rather_than_recite(
    tmp_path: Path,
) -> None:
    """The spoken tool rule is what stops a folder listing being read out item by item.
    It belongs to the voice surface only — the text path renders markdown lists fine."""
    from app.services.persona.defaults import DEFAULT_PERSONALITY, build_prompt

    spoken = build_prompt(DEFAULT_PERSONALITY, spoken=True, tools_enabled=True)
    written = build_prompt(DEFAULT_PERSONALITY, spoken=False, tools_enabled=True)

    assert "never read out a list item by item" in spoken.lower().replace("  ", " ")
    assert "file path" in spoken and "tool name" in spoken
    assert "read out a list" not in written.lower()


def test_a_call_is_told_speaking_changes_how_she_talks_not_what_she_does() -> None:
    """On a call she answered "Got it, I'll remember that" and called nothing (bug 50):
    the call's style names the actions to take first. Only where there are tools to
    take, and never in writing (mutations: drop it; say it with no tools; say it in
    chat)."""
    from app.services.persona.defaults import DEFAULT_PERSONALITY, build_prompt

    rule = "Speaking aloud changes how you talk, not what you do"
    call = build_prompt(DEFAULT_PERSONALITY, spoken=True, tools_enabled=True)
    toolless = build_prompt(DEFAULT_PERSONALITY, spoken=True, tools_enabled=False)
    written = build_prompt(DEFAULT_PERSONALITY, spoken=False, tools_enabled=True)

    assert rule in call and "memory_save" in call.split(rule)[1].split("\n")[0]
    assert rule not in toolless and "after any tool" not in toolless
    assert rule not in written


async def test_barge_in_cancels_the_capability_itself_not_just_the_speech(
    tmp_path: Path,
) -> None:
    """Barge-in cancels the capability itself, not just the speech.

    Wrapping the call in `asyncio.shield` would absorb the cancellation and let an
    interrupted write finish anyway. This asserts on whether the action completed,
    not on `turn_in_flight` or what was spoken.
    """
    ran_to_completion = False

    async def slow_side_effect(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal ran_to_completion
        await asyncio.sleep(0.5)
        ran_to_completion = True
        raise AssertionError("unreachable in this test")

    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    _ws, (memory, _k), session = await _session(tmp_path, [_read_call(note), {"tokens": ["done"]}])
    session.tools.execute = slow_side_effect  # type: ignore[union-attr,method-assign,assignment]

    session.turn_in_flight = True
    session.spawn_respond("read my note")
    await asyncio.sleep(0.1)  # let the capability start
    await session.trigger_barge_in(b"\x00\x00")
    await asyncio.sleep(0.7)  # well past when it would have finished

    assert ran_to_completion is False, "the capability outlived the turn that was cancelled"
    await memory.store.close()
