"""Spoken confirmation: asking permission aloud and hearing the answer.

`respond` arms the barge-in gate for the whole turn, so `spoken_confirm` suspends it
while waiting for an answer; otherwise saying "yes" would be heard as an
interruption and cancel the round it answers. Several tests below pin that down.

Consent is never inferred: anything other than an explicit yes ends in a denial or
an expiry, and both are audited.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.schemas.events import Event
from app.services.tools import ToolBroker
from app.services.voice.realtime import spoken_confirm, workers
from app.services.voice.realtime.confirm_reply import classify_answer, looks_like_a_new_request
from app.services.voice.realtime.session import RealtimeSession
from app.services.voice.realtime.voice_events import VoiceEvent
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


async def _session(tmp_path: Path, script: list[dict[str, Any]], *, confirm_timeout_s: float = 5.0):
    bus = InProcessEventBus()
    memory = await build_fake_memory_runtime(tmp_path / "db", bus)
    kernel = await build_fake_kernel_runtime(
        memory, bus, file_roots=[str(tmp_path)], confirm_timeout_s=confirm_timeout_s
    )
    broker = ToolBroker(
        kernel.kernel,
        kernel.registry,
        actor="veronica:voice",
        voice_only=True,
    )
    session = RealtimeSession(
        FakeWS(),  # type: ignore[arg-type]
        build_fake_runtime(  # type: ignore[arg-type]
            stt=FakeSTT([], None), tts=FakeTTS(), llm=ScriptedLLM(script)
        ),
        bus,
        get_settings(),
        persona=lambda: "You are Veronica.",
        metrics_sink=ListMetricsSink(),
        detector=FakeDetector([]),
        memory=memory,
        tools=broker,
    )
    return memory, kernel, session


def _spoken(session: RealtimeSession) -> str:
    chunks: list[bytes] = []
    while not session.audio_out.empty():
        item = session.audio_out.get_nowait()
        if isinstance(item, bytes):  # not the marks that open each reply
            chunks.append(item)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _write_call(path: Path, body: str = "hello") -> dict[str, Any]:
    return {
        "tool_calls": [
            {"function": {"name": "files_write", "arguments": {"path": str(path), "content": body}}}
        ]
    }


async def _until_asking(session: RealtimeSession, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while session.awaiting_confirm is None:
            await asyncio.sleep(0.01)


# -- the classifier ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["yes", "Yeah", "yep, go ahead", "sure", "do it", "ok", "please do", "um, yes", "نعم", "تمام"],
)
def test_an_answer_that_means_yes(text: str) -> None:
    assert classify_answer(text) is True


@pytest.mark.parametrize(
    "text",
    ["no", "nope", "don't", "stop", "cancel", "never mind", "leave it", "not now", "لا", "بلاش"],
)
def test_an_answer_that_means_no(text: str) -> None:
    assert classify_answer(text) is False


@pytest.mark.parametrize(
    "text", ["no, go ahead and don't", "don't do it", "not now", "cancel that"]
)
def test_a_refusal_is_never_read_as_consent(text: str) -> None:
    assert classify_answer(text) is False


@pytest.mark.parametrize(
    "text",
    ["okay, don't", "okay don't do it", "yeah, don't bother", "sure, but no", "ok, cancel that"],
)
def test_agreeing_then_refusing_is_never_consent(text: str) -> None:
    """A refusal after an agreeable opener is never consent.

    Refusal words are matched anywhere in the utterance, not only at the start, so
    "okay, don't" can't read as a yes. The caller re-asks once, and the kernel fails
    closed if that goes unanswered.
    """
    assert classify_answer(text) is not True


def test_an_agreeable_phrase_containing_a_stray_no_is_not_taken_as_consent_either() -> None:
    # Fail closed both ways: "yes, no problem" is ambiguous to a regex, so it re-asks
    # rather than guessing.
    assert classify_answer("yes, no problem") is None


@pytest.mark.parametrize("text", ["hmm", "what time is it", "the roof", "", "maybe later on"])
def test_anything_unclear_is_not_consent(text: str) -> None:
    assert classify_answer(text) is None


def test_a_whole_new_question_is_recognised_as_changing_the_subject() -> None:
    assert looks_like_a_new_request("actually what's the weather like") is True
    assert looks_like_a_new_request("hmm") is False  # worth one re-ask, not an abandon


# -- what she says -------------------------------------------------------------


def test_the_spoken_ask_never_reads_out_a_path() -> None:
    """A full Windows path read aloud is unusable. The filename is the actionable part."""
    summary = r"write 19 characters to D:\Projects\Veronica\data\notes.md"
    spoken = spoken_confirm.spoken_summary("files.write", summary)

    assert "notes.md" in spoken
    assert "\\" not in spoken and "D:" not in spoken
    assert "19 characters" not in spoken  # a byte count is noise out loud
    assert spoken.endswith("?")


# -- approve / deny ------------------------------------------------------------


async def test_saying_yes_writes_the_file(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["Done — I wrote it."]}]
    )

    turn = asyncio.create_task(workers.respond(session, "write a note saying hello"))
    await _until_asking(session)
    assert await spoken_confirm.answer(session, "yes") is True
    await asyncio.wait_for(turn, timeout=10)

    assert target.read_text(encoding="utf-8") == "hello"
    rows = await kernel.audit.list()
    assert rows[0]["decision"] == "confirmed"
    assert rows[0]["confirm_via"] == "voice"  # the channel is on the record
    assert rows[0]["actor"] == "veronica:voice"
    await memory.store.close()


async def test_she_is_shown_speaking_again_once_answered(tmp_path: Path) -> None:
    """Waiting for the answer moves the state to listening, then thinking.

    What she says after it is speech too. When the turn had spoken before the question,
    the screen showed "thinking" while she said "Done, it's written."
    """
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(
        tmp_path,
        [{"tokens": ["Sure."], **_write_call(target)}, {"tokens": ["Done, it's written."]}],
    )
    seen: list[str] = []

    async def state(event: Event) -> None:
        seen.append(event.payload["state"])

    session._bus.subscribe(VoiceEvent.state, state)
    turn = asyncio.create_task(workers.respond(session, "write a note saying hello"))
    await _until_asking(session)
    assert await spoken_confirm.answer(session, "yes") is True
    await asyncio.wait_for(turn, timeout=10)

    assert seen[-3:] == ["thinking", "speaking", "listening"]
    await memory.store.close()


async def test_saying_no_writes_nothing_and_is_audited_as_denied(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["Alright, I've left it."]}]
    )

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    await spoken_confirm.answer(session, "no, leave it")
    await asyncio.wait_for(turn, timeout=10)

    assert target.exists() is False
    rows = await kernel.audit.list()
    assert rows[0]["decision"] == "denied" and rows[0]["code"] == "confirm_denied"
    assert rows[0]["confirm_via"] == "voice"
    await memory.store.close()


async def test_an_unclear_answer_is_re_asked_once(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    memory, _k, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["Done."]}], confirm_timeout_s=3.0
    )

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    _spoken(session)  # drop the original ask

    await spoken_confirm.answer(session, "hmm")
    assert "was that a yes" in _spoken(session).lower()
    assert session.awaiting_confirm is not None  # still waiting, nothing decided

    await spoken_confirm.answer(session, "yes")
    await asyncio.wait_for(turn, timeout=10)
    assert target.exists()
    await memory.store.close()


async def test_changing_the_subject_abandons_the_action_and_keeps_the_question(
    tmp_path: Path,
) -> None:
    """An unrelated reply means the user moved on.

    The action is denied (never act on a non-answer) and their question is carried
    into the conversation.
    """
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["It's sunny."]}]
    )

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    await spoken_confirm.answer(session, "actually what's the weather like")
    await asyncio.wait_for(turn, timeout=10)

    assert target.exists() is False  # never acted on a non-answer
    rows = await kernel.audit.list()
    assert rows[0]["decision"] == "denied"
    assert any(m["role"] == "user" and "weather" in m["content"] for m in session.history), (
        "the question they actually asked was dropped"
    )
    # And answered in this turn: the model's next round sees it. It used to wait a turn.
    last_round = session.runtime.llm.calls[-1]  # type: ignore[attr-defined]
    assert last_round[-1] == {"role": "user", "content": "actually what's the weather like"}
    await memory.store.close()


async def test_silence_fails_closed_and_says_so(tmp_path: Path) -> None:
    """Silence is never consent: the kernel's window closes on its own.

    The assistant then says the request lapsed rather than leaving the user wondering.
    """
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["No problem."]}], confirm_timeout_s=0.4
    )

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    await asyncio.wait_for(turn, timeout=10)  # nobody answers

    assert target.exists() is False
    assert "leave that then" in _spoken(session).lower()
    rows = await kernel.audit.list()
    assert rows[0]["code"] == "confirm_timeout"
    await memory.store.close()


# -- one prompt, two surfaces --------------------------------------------------


async def test_approving_on_screen_ends_the_spoken_ask(tmp_path: Path) -> None:
    """The on-screen card and the spoken question share one token.

    Answering either one finishes both, so nothing is left pending on screen or aloud.
    """
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["Written."]}]
    )

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    token = session.awaiting_confirm.token  # type: ignore[union-attr]

    # Resolve it the way the UI route (`POST /capabilities/confirm`) does.
    assert kernel.kernel.resolve_confirmation(token, True, via="ui") is True
    await asyncio.wait_for(turn, timeout=10)

    assert target.read_text(encoding="utf-8") == "hello"
    rows = await kernel.audit.list()
    assert rows[0]["confirm_via"] == "ui"  # the screen answered, and the record says so
    # It went ahead, so she doesn't say she'll leave it (she used to, as it was written).
    assert "leave that" not in _spoken(session)
    await memory.store.close()


async def test_declining_on_screen_is_still_said_out_loud(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["All right."]}]
    )

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    token = session.awaiting_confirm.token  # type: ignore[union-attr]
    assert kernel.kernel.resolve_confirmation(token, False, via="ui") is True
    await asyncio.wait_for(turn, timeout=10)

    assert target.exists() is False
    assert "I'll leave that then." in _spoken(session)
    await memory.store.close()


async def test_a_second_answer_is_a_no_op(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    memory, kernel, session = await _session(tmp_path, [_write_call(target), {"tokens": ["Done."]}])

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    token = session.awaiting_confirm.token  # type: ignore[union-attr]

    assert kernel.kernel.resolve_confirmation(token, True, via="voice") is True
    assert kernel.kernel.resolve_confirmation(token, False, via="ui") is False  # spent
    await asyncio.wait_for(turn, timeout=10)

    assert target.exists()  # the first answer stands; the replay changed nothing
    await memory.store.close()


async def test_resolving_announces_itself_so_the_card_can_close(tmp_path: Path) -> None:
    """Resolving publishes `capability.confirm_resolved` so the card can close.

    Without it, the on-screen card would outlive a spoken answer to the same token.
    """
    target = tmp_path / "note.md"
    bus = InProcessEventBus()
    seen: list[dict[str, Any]] = []

    async def capture(event: Any) -> None:
        seen.append(event.payload)

    bus.subscribe("capability.confirm_resolved", capture)
    memory, kernel, session = await _session(tmp_path, [_write_call(target), {"tokens": ["ok"]}])
    session._bus = bus  # type: ignore[assignment]
    kernel.confirmations._bus = bus  # type: ignore[assignment]

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    await spoken_confirm.answer(session, "yes")
    await asyncio.wait_for(turn, timeout=10)
    await asyncio.sleep(0.05)  # the announcement is fire-and-forget

    assert seen and seen[0]["approved"] is True and seen[0]["via"] == "voice"
    await memory.store.close()


# -- the gate ------------------------------------------------------------------


async def test_the_barge_in_gate_is_down_while_she_waits_for_an_answer(
    tmp_path: Path,
) -> None:
    """The barge-in gate is suspended while waiting for a spoken answer.

    `respond` arms the gate for the whole turn, so without the suspension the user's
    "yes" would be heard as an interruption and cancel the round it answers.
    """
    target = tmp_path / "note.md"
    memory, _k, session = await _session(tmp_path, [_write_call(target), {"tokens": ["Done."]}])

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)

    assert session.barge.armed is False, "speech would be treated as a barge-in"

    await spoken_confirm.answer(session, "yes")
    await asyncio.wait_for(turn, timeout=10)

    # The turn then completes and is released, so the next utterance starts a new turn.
    assert session.turn_in_flight is False
    await memory.store.close()


async def test_answering_once_does_not_arm_the_gate_against_the_next_answer(
    tmp_path: Path,
) -> None:
    """An utterance answering a pending confirmation does not re-arm barge-in.

    If `end_utterance` armed the gate for it, the reply to a re-ask would be heard as
    an interruption and cancel the confirmation it was answering.
    """
    target = tmp_path / "note.md"
    memory, _k, session = await _session(
        tmp_path, [_write_call(target), {"tokens": ["Done."]}], confirm_timeout_s=20.0
    )

    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    assert session.barge.armed is False

    await spoken_confirm.answer(session, "hmm")  # unclear -> she re-asks
    session.begin_utterance(b"\x00\x00")  # the mic lifecycle for that reply
    session.end_utterance(last_voiced=session.clock())

    assert session.barge.armed is False, "the next answer would be heard as a barge-in"
    assert session.awaiting_confirm is not None

    await spoken_confirm.answer(session, "yes")  # the second answer lands
    await asyncio.wait_for(turn, timeout=10)
    assert target.exists()
    await memory.store.close()


async def test_an_ordinary_turn_still_arms_barge_in(tmp_path: Path) -> None:
    """The relaxation is confined to the confirmation window: normal speech must still
    arm the gate, or barge-in stops working everywhere else."""
    memory, _k, session = await _session(tmp_path, [{"tokens": ["hi"]}])

    assert session.awaiting_confirm is None
    session.begin_utterance(b"\x00\x00")
    session.end_utterance(last_voiced=session.clock())

    assert session.barge.armed is True
    assert session.turn_in_flight is True
    await memory.store.close()
