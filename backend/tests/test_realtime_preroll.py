"""The pre-roll: an utterance starts with the audio from just before speech was detected.

The detector says "speech" only after 250 ms of voicing, so an utterance that began with
the triggering frame lost the start of what was said. Frames here are numbered, so each
test is about exactly which audio reached recognition, and in what order.
"""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.voice.audio import Transcript
from app.services.voice.realtime import ingest, session_control
from app.services.voice.realtime.session import RealtimeSession
from app.services.voice.turn import TurnEvent
from tests.fakes import FakeDetector, FakeLLM, FakeTTS, FakeWS, ListMetricsSink
from tests.test_realtime_session import make_session, start_call, states, wait_until


class _RecordingSTT:
    """Records the first byte of every frame each utterance hands to recognition.

    `finals` are the transcripts of the first utterances, in order; the rest are heard
    as nothing, which ends their turn as a cough would.
    """

    def __init__(self, finals: list[str] | None = None) -> None:
        self.utterances: list[list[int]] = []
        self.finished = 0
        self._finals = list(finals or [])

    def stream_transcripts(self, audio: Any) -> Any:
        async def gen() -> Any:
            heard: list[int] = []
            self.utterances.append(heard)
            async for frame in audio:
                heard.append(frame[0])
            self.finished += 1
            if self._finals:
                yield Transcript(self._finals.pop(0), is_final=True)

        return gen()


async def feed_numbered(session: RealtimeSession, *numbers: int) -> None:
    for number in numbers:
        session.audio_in.put_nowait(bytes([number, 0]) * 320)  # 20 ms, as the client sends


async def test_an_utterance_starts_where_the_speech_did_and_only_once() -> None:
    """The audio before `speech_start` leads the utterance, and no frame comes twice.

    Without it the call probe's "What's the capital of France?" was heard as "the
    capital of France?".
    """
    ws = FakeWS()
    stt = _RecordingSTT()
    session = make_session(
        ws,
        detector=FakeDetector([[], [], [], [], [TurnEvent.speech_start]]),
        stt=stt,  # type: ignore[arg-type]
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed_numbered(session, 0, 1, 2, 3, 4)
    await wait_until(lambda: bool(stt.utterances) and len(stt.utterances[0]) >= 5)
    assert stt.utterances == [[0, 1, 2, 3, 4]]
    await session._stop_call()


async def test_an_interruption_while_she_speaks_leaves_no_audio_for_the_next_utterance() -> None:
    """Each frame reaches one utterance, and only audio from just before it leads it.

    An interruption while she speaks opens with its own frame: the ring was recorded
    over her voice. The ring once kept what it held then, and took the frame that closed
    an utterance as well, so both were spliced onto the front of the next one: here that
    was [2, 10, 11, 12, 14, 20, 21, 22].
    """
    ws = FakeWS()
    stt = _RecordingSTT(finals=["play music"])
    block = asyncio.Event()
    start, end = [TurnEvent.speech_start], [TurnEvent.speech_end]
    session = make_session(
        ws,
        # frames 0-2 a turn; 10-12 while she speaks, 13-14 the interruption; 20-22 a turn
        detector=FakeDetector([start, [], end, [], [], [], start, end, [], [], start]),
        stt=stt,  # type: ignore[arg-type]
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing.", " Playing"], block=block),  # held open to interrupt
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed_numbered(session, 0, 1, 2)
    await wait_until(lambda: states(ws)[-1:] == ["speaking"])
    # The app reports what it has played: her first words were heard, so the
    # interruption, heard as nothing, leaves nothing unanswered to go back to.
    session_control.note_playback(
        session, {"reply": session.reply_id, "samples": session.ledger.total_samples}
    )
    await feed_numbered(session, 10, 11, 12, 13, 14)
    # The interruption is heard as nothing, which ends its turn and frees the next one.
    await wait_until(lambda: stt.finished == 2 and not session.turn_in_flight)
    await feed_numbered(session, 20, 21, 22)
    await wait_until(lambda: len(stt.utterances) == 3 and len(stt.utterances[2]) >= 3)

    assert stt.utterances == [[0, 1, 2], [13, 14], [20, 21, 22]]
    block.set()
    await session._stop_call()


async def test_picking_up_again_before_she_speaks_keeps_the_first_word() -> None:
    """Speech after a pause, while she is still thinking, is an interruption too.

    The barge-in gate is armed as soon as an utterance ends, so "turn on the lights ...
    and the fan" interrupts the reply to its first half. Before she says anything the
    ring holds only the user, and "and" was being cut from the second half.
    """
    ws = FakeWS()
    stt = _RecordingSTT(finals=["turn on the lights"])
    block = asyncio.Event()
    start, end = [TurnEvent.speech_start], [TurnEvent.speech_end]
    session = make_session(
        ws,
        detector=FakeDetector([start, [], end, [], [], [], start, end]),
        stt=stt,  # type: ignore[arg-type]
        tts=FakeTTS(),
        llm=FakeLLM([], block=block),  # thinking: no word of the reply yet
        sink=ListMetricsSink(),
    )
    block.set()  # open for the warm-up's own call to the model
    await start_call(session, ws)
    block.clear()  # and shut for the reply, which then says nothing yet
    await feed_numbered(session, 0, 1, 2)
    await wait_until(lambda: states(ws)[-1:] == ["thinking"])
    await feed_numbered(session, 10, 11, 12, 13, 14)
    await wait_until(lambda: stt.finished == 2)

    assert stt.utterances == [[0, 1, 2], [10, 11, 12, 13, 14]]
    block.set()
    await session._stop_call()


async def test_the_answer_to_a_spoken_question_keeps_its_first_word() -> None:
    # A one-word "yes" is where the lost 250 ms mattered most.
    ws = FakeWS()
    stt = _RecordingSTT()
    session = make_session(
        ws,
        detector=FakeDetector([[], [], [], [TurnEvent.speech_start]]),
        stt=stt,  # type: ignore[arg-type]
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    session.awaiting_confirm = SimpleNamespace()  # type: ignore[assignment]
    session.turn_in_flight = True  # a confirmation is asked inside a turn
    await feed_numbered(session, 0, 1, 2, 3)
    await wait_until(lambda: bool(stt.utterances) and len(stt.utterances[0]) >= 4)

    assert stt.utterances == [[0, 1, 2, 3]]
    session.awaiting_confirm = None
    await session._stop_call()


async def test_only_the_last_400_ms_lead_an_utterance() -> None:
    # Seconds of room noise ahead of every utterance would only give recognition more
    # to get wrong: of 24 frames before the speech, the last 20 (400 ms) go in.
    ws = FakeWS()
    stt = _RecordingSTT()
    session = make_session(
        ws,
        detector=FakeDetector([*([[]] * 24), [TurnEvent.speech_start]]),
        stt=stt,  # type: ignore[arg-type]
        tts=FakeTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed_numbered(session, *range(25))
    await wait_until(lambda: bool(stt.utterances) and len(stt.utterances[0]) >= 21)

    assert stt.utterances == [list(range(4, 25))]
    await session._stop_call()


def test_the_ring_is_emptied_by_the_utterance_that_takes_it() -> None:
    # Left full, one utterance's opening would be spliced onto the front of the next.
    ring: deque[bytes] = deque([b"\x01\x00", b"\x02\x00"])
    assert ingest.drain(ring) == [b"\x01\x00", b"\x02\x00"]
    assert not ring


@pytest.mark.parametrize("frame_ms", [10, 20, 32, 100])
def test_the_ring_holds_400_ms_whatever_the_frame_size(frame_ms: int) -> None:
    # Counted in frames, 10 ms frames kept 200 ms, under the 250 ms the detector needs,
    # and 100 ms frames kept two seconds of what was playing.
    frame = bytes(16 * frame_ms * 2)  # 16 samples a millisecond, two bytes a sample
    ring: deque[bytes] = deque()
    for _ in range(100):
        ingest.keep(ring, frame)
    held_ms = sum(map(len, ring)) / (16 * 2)
    assert 400 <= held_ms < 400 + frame_ms
