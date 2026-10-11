"""Turn-lifecycle safety in the realtime call.

Covers the ways one call can break itself: a mid-sentence pause opening a second
concurrent reply over the shared chunker, a turn that never reaches `respond`
leaving the turn gate shut, an STT decode outliving teardown, an odd-length audio
frame killing the ingest loop while the client still shows "listening", and a
metrics row that can't be written taking the turn down with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.services.voice.audio import Transcript, pcm16_bytes_to_float32
from app.services.voice.realtime import workers
from app.services.voice.realtime.session_emit import State
from app.services.voice.realtime.voice_events import VoiceEvent
from app.services.voice.turn import TurnEvent
from app.services.voice.vad_worker import VadResult
from tests.fakes import FakeDetector, FakeLLM, FakeSTT, FakeTTS, FakeWS, ListMetricsSink
from tests.test_realtime_session import feed, make_session, start_call, states, wait_until
from tests.test_reply_cap import _HeldTTS


def _session(ws: FakeWS, script: list[list[TurnEvent]], *, seen: list[Any] | None = None) -> Any:
    return make_session(
        ws,
        detector=FakeDetector(script),
        stt=FakeSTT([], "turn on the lights"),
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing."], seen=seen),
        sink=ListMetricsSink(),
    )


async def test_pause_mid_sentence_does_not_start_a_second_turn() -> None:
    # "Turn on the lights" ·(pause)· "and the fan": any pause longer than the end-of-speech
    # wait does this. The second speech_start lands while the first decode is still
    # running; without the in-flight gate it would open a second utterance and run two
    # replies over one shared chunker, writing two bogus assistant turns to history.
    ws = FakeWS()
    seen: list[Any] = []
    script = [
        [TurnEvent.speech_start],
        [],
        [TurnEvent.speech_end],
        [TurnEvent.speech_start],  # the pause ends -> user keeps talking
        [],
        [TurnEvent.speech_end],
    ]
    session = _session(ws, script, seen=seen)
    await start_call(session, ws)
    await feed(session, 6)
    await wait_until(lambda: any(e["type"] == "metrics" for e in events_of(ws)))
    await session._stop_call()

    # The continuation is handled as a barge-in (the first reply is cancelled), not as
    # a second concurrent reply. Two identical assistant turns in history would mean
    # both replies ran over the shared chunker.
    assistant = [m["content"] for m in session.history if m["role"] == "assistant"]
    assert len(assistant) == len(set(assistant)), f"duplicated assistant turns: {assistant}"
    assert session._respond_task is None  # nothing left running


def events_of(ws: FakeWS) -> list[dict[str, Any]]:
    import json

    return [json.loads(t) for t in ws.sent_text]


async def test_odd_length_frame_does_not_kill_the_ingest_loop() -> None:
    # np.frombuffer raises on an odd byte count, which would kill ingest_loop while the
    # client still shows "listening" (VAD stopped, queue growing).
    assert len(pcm16_bytes_to_float32(b"\x00\x00\x00")) == 1  # trailing byte dropped

    ws = FakeWS()
    session = _session(ws, [[TurnEvent.speech_start], [], [TurnEvent.speech_end]])
    await start_call(session, ws)

    session.audio_in.put_nowait(b"\x00\x00\x00")  # mis-framed frame reaches the loop
    await feed(session, 3)
    # The loop survived and the turn completed normally, back to listening.
    await wait_until(lambda: states(ws)[-1] == "listening" and len(states(ws)) >= 4)

    assert session._ingest_task is not None and not session._ingest_task.done()
    await session._stop_call()


async def test_empty_transcript_releases_the_turn_gate() -> None:
    # The single-flight gate is armed at speech_end, but an utterance that decodes to
    # nothing (silence, noise, a cough) never reaches `respond`. The gate must still be
    # released, or the ingest loop skips every later speech_start and the call stops
    # hearing the user.
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector([[TurnEvent.speech_start], [], [TurnEvent.speech_end]]),
        stt=FakeSTT([], ""),  # no usable final transcript
        tts=FakeTTS(),
        llm=FakeLLM(["unused"]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: not session.turn_in_flight)

    assert session.turn_in_flight is False  # the gate reopened
    await session._stop_call()


async def test_stt_failure_releases_the_turn_gate() -> None:
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector([[TurnEvent.speech_start], [], [TurnEvent.speech_end]]),
        stt=FakeSTT([], "boom", boom=True),  # raises mid-stream
        tts=FakeTTS(),
        llm=FakeLLM(["unused"]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: any(e["type"] == "error" for e in events_of(ws)))

    assert session.turn_in_flight is False  # a failed turn must not wedge the call
    await session._stop_call()


async def test_teardown_cancels_a_still_decoding_utterance() -> None:
    # Every STT task stays tracked after a newer utterance replaces it. An untracked one
    # could finish a whole turn after the call ended, holding the models and changing
    # history after the session summary had been taken.
    ws = FakeWS()
    session = _session(ws, [[TurnEvent.speech_start], [], [TurnEvent.speech_end]])
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: bool(session._stt_tasks) or session.turn_in_flight)

    await session._stop_call()
    await asyncio.sleep(0.01)

    assert not session._stt_tasks
    assert session._respond_task is None


class _HeldSTT(FakeSTT):
    """Transcribes only when released: an utterance still being decoded."""

    def __init__(self, text: str = "turn on the lights") -> None:
        super().__init__([], text)
        self._text = text
        self.release = asyncio.Event()

    def stream_transcripts(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        async def gen() -> AsyncIterator[Transcript]:
            async for _ in audio:
                pass
            await self.release.wait()
            yield Transcript(self._text, is_final=True)

        return gen()


async def test_speech_heard_as_the_call_stops_opens_no_turn_after_it() -> None:
    # Teardown waits on the turn's tasks. With the ingest loop still running during that
    # wait, a speech_start already scored by the detection thread was taken as an
    # interruption and opened an utterance whose STT task nothing cancelled: a decode,
    # and then a reply, after the call had ended.
    ws = FakeWS()
    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=_HeldSTT(),
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing."]),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    frame = b"\x00\x00" * 320
    session.begin_utterance(frame)
    session.end_utterance(last_voiced=session.clock())  # decoding; the gate is armed
    assert session.vad_worker is not None
    session.vad_worker.results.put_nowait(
        VadResult(frame, [TurnEvent.speech_start], session.clock(), 0.0)
    )

    await session._stop_call()
    await asyncio.sleep(0.01)

    decoding = [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_coro().__qualname__ == "run_stt"  # type: ignore[union-attr]
    ]
    assert decoding == []


class _ScriptedSTT(FakeSTT):
    """One transcript per utterance, in order; the first waits until released."""

    def __init__(self, texts: list[str]) -> None:
        super().__init__([], None)
        self._texts = list(texts)
        self.release_first = asyncio.Event()
        self._first = True

    def stream_transcripts(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        text, held = self._texts.pop(0), self._first
        self._first = False

        async def gen() -> AsyncIterator[Transcript]:
            async for _ in audio:
                pass
            if held:
                await self.release_first.wait()
            yield Transcript(text, is_final=True)

        return gen()


async def test_a_decode_finishing_during_an_interruption_is_not_answered_alone() -> None:
    # "Turn on the lights" ... "and the fan": the second half interrupts while the first
    # is still being decoded. The interruption awaits socket writes before it used to
    # open the new utterance, and a first decode finishing in that gap still took itself
    # for the newest and was answered alone, while the user was talking.
    ws = FakeWS()
    seen: list[Any] = []
    stt = _ScriptedSTT(["turn on the lights", "and the fan"])
    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=stt,
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing."], seen=seen),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    frame = b"\x00\x00" * 320
    session.begin_utterance(frame)
    session.end_utterance(last_voiced=session.clock())  # decoding; the gate is armed

    async def first_decode_lands(_event: Any) -> None:
        stt.release_first.set()
        for _ in range(20):  # let it finish while the interruption is still awaiting
            await asyncio.sleep(0)

    session._bus.subscribe(VoiceEvent.barge_in, first_decode_lands)
    await session.trigger_barge_in(frame)
    session.end_utterance(last_voiced=session.clock())
    replies = lambda: [call for call in seen if call and call[0].get("role") == "system"]  # noqa: E731
    await wait_until(lambda: len(replies()) >= 1 and session._respond_task is None)
    await asyncio.sleep(0.02)

    assert len(replies()) == 1  # one reply, not one per half (the warm-up isn't one)
    prompt = " ".join(str(message.get("content")) for message in replies()[0])
    assert "turn on the lights" in prompt and "and the fan" in prompt
    await session._stop_call()


async def test_a_cough_after_a_question_does_not_lose_the_question() -> None:
    # "What time is it?", then a cough while it is decoded. The cough is the newest
    # utterance and decodes to nothing, and the question it superseded went into history
    # with nobody left to answer it.
    ws = FakeWS()
    seen: list[Any] = []
    stt = _ScriptedSTT(["what time is it", ""])
    session = make_session(
        ws,
        detector=FakeDetector([]),
        stt=stt,
        tts=FakeTTS(),
        llm=FakeLLM(["It's noon."], seen=seen),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    frame = b"\x00\x00" * 320
    session.begin_utterance(frame)
    session.end_utterance(last_voiced=session.clock())
    await session.trigger_barge_in(frame)  # the cough
    stt.release_first.set()
    session.end_utterance(last_voiced=session.clock())

    await wait_until(
        lambda: any(m["role"] == "assistant" and "noon" in m["content"] for m in session.history)
    )
    assert [m["content"] for m in session.history if m["role"] == "user"] == ["what time is it"]
    await session._stop_call()


class _FullDiskSink(ListMetricsSink):
    """A metrics file that can't be written: a full disk, or one held open elsewhere."""

    async def write(self, metrics: Any) -> None:
        raise OSError(28, "No space left on device")


async def test_a_turn_whose_row_cannot_be_written_still_goes_back_to_listening() -> None:
    # The write raised out of the finished reply, before it went back to listening: the
    # screen showed her speaking for the rest of the call.
    session = make_session(
        FakeWS(),
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing."]),
        sink=_FullDiskSink(),
    )

    await workers.respond(session, "turn on the lights")

    assert session._state is State.listening


async def test_an_interruption_whose_row_cannot_be_written_still_hands_over() -> None:
    # Raised here, the write went up through the ingest loop, and the call stopped hearing.
    tts = _HeldTTS()
    session = make_session(
        FakeWS(),
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=tts,
        llm=FakeLLM(["Here is the long answer."]),
        sink=_FullDiskSink(),
    )
    turn = asyncio.create_task(workers.respond(session, "tell me everything"))
    await wait_until(tts.speaking.is_set)

    await session.trigger_barge_in(b"\x00\x00" * 320)

    assert session._state is State.listening and session.utterance_q is not None
    tts.release.set()
    await asyncio.gather(turn, return_exceptions=True)
    await session._cancel_active_turn()
