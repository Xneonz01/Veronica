"""A reply is interruptible while it plays, and history keeps what was heard of it.

The client reports how far it has played each reply; the server's ledger turns that
into words. Also the two races an interruption used to lose: a finished turn recorded a
second time, and an older utterance's slow decode answered while the user was talking.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.core.event_bus import InProcessEventBus
from app.services.voice.audio import Transcript
from app.services.voice.realtime import session_control, spoken_confirm, workers
from app.services.voice.realtime.barge_in import BargeInGate
from app.services.voice.realtime.session_emit import ReplyStart
from app.services.voice.turn import TurnEvent
from tests.fakes import (
    FakeDetector,
    FakeLLM,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    build_fake_memory_runtime,
)
from tests.test_realtime_session import events, feed, make_session, start_call, states, wait_until
from tests.test_realtime_turn_safety import _HeldSTT
from tests.test_voice_confirm import _session as confirm_session
from tests.test_voice_confirm import _until_asking, _write_call

START, END = [TurnEvent.speech_start], [TurnEvent.speech_end]


def _replies(session: Any) -> list[str]:
    return [m["content"] for m in session.history if m["role"] == "assistant"]


async def _finished_turn(ws: FakeWS, detector: FakeDetector, sink: Any = None) -> Any:
    """A call with one turn done: "Sure thing." then "Playing it now." (FakeTTS makes each
    sentence's audio from its own bytes: 5 samples, then 7)."""
    session = make_session(
        ws,
        detector=detector,
        stt=FakeSTT([], "play music"),
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing.", " Playing it now."]),
        sink=sink or ListMetricsSink(),
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: session.reply_entry is not None)
    return session


async def test_each_replys_mark_reaches_the_client_before_its_audio() -> None:
    # The client counts a reply's playback from its mark, so the two can't be sent by
    # separate paths that might overtake each other.
    ws = FakeWS()
    session = await _finished_turn(ws, FakeDetector([START, [], END]))
    await wait_until(lambda: session.audio_out.empty() and bool(ws.sent_bytes))

    marks = [i for i, item in enumerate(ws.sent) if isinstance(item, str) and "reply_audio" in item]
    first_audio = next(i for i, item in enumerate(ws.sent) if isinstance(item, bytes))
    assert len(marks) == 1 and marks[0] < first_audio
    assert json.loads(str(ws.sent[marks[0]])) == {"type": "reply_audio", "reply": session.reply_id}
    await session._stop_call()


async def test_talking_over_a_finished_reply_keeps_only_what_was_heard() -> None:
    """The gate used to close when the last sentence was made, while the client played on:
    talking over the rest didn't stop it. And history kept every word she was sent."""
    ws = FakeWS()
    session = await _finished_turn(ws, FakeDetector([START, [], END, START]))
    assert _replies(session) == ["Sure thing. Playing it now."]

    await session_control.dispatch(
        session, {"action": "playback_position", "reply": session.reply_id, "samples": 5}
    )
    await feed(session, 1)  # speech while "Playing it now." is still playing
    await wait_until(lambda: any(e["type"] == "barge_in" for e in events(ws)))

    assert _replies(session) == ["Sure thing.…"]  # cut back in place, not a second copy
    await session._stop_call()


async def test_speech_after_a_reply_has_played_is_a_new_turn() -> None:
    ws = FakeWS()
    session = await _finished_turn(ws, FakeDetector([START, [], END, START]))
    await session_control.dispatch(
        session, {"action": "playback_position", "reply": session.reply_id, "samples": 12}
    )
    await feed(session, 1)
    await wait_until(lambda: session.utterance_q is not None)

    assert not any(e["type"] == "barge_in" for e in events(ws))
    assert _replies(session) == ["Sure thing. Playing it now."]
    await session._stop_call()


def test_a_position_report_is_taken_only_as_a_count_for_the_reply_playing() -> None:
    s = SimpleNamespace(reply_id=3, played_samples=None, barge=BargeInGate())
    for junk in (
        {"reply": 2, "samples": 10},  # an earlier reply's
        {"reply": 3, "samples": True},
        {"reply": 3, "samples": -1},
        {"reply": 3, "samples": "10"},
        {"reply": "3", "samples": 10},
        {"samples": 10},
    ):
        session_control.note_playback(s, junk)  # type: ignore[arg-type]
    assert s.played_samples is None

    session_control.note_playback(s, {"reply": 3, "samples": 10})  # type: ignore[arg-type]
    session_control.note_playback(s, {"reply": 3, "samples": 4})  # type: ignore[arg-type]
    assert s.played_samples == 10  # never back


class _SlowSink(ListMetricsSink):
    def __init__(self) -> None:
        super().__init__()
        self.writing = asyncio.Event()
        self.release = asyncio.Event()

    async def write(self, metrics: Any) -> None:
        self.writing.set()
        await self.release.wait()
        await super().write(metrics)


async def test_a_turn_is_recorded_once_when_speech_lands_on_its_last_write() -> None:
    # The turn was closed after its metrics were written, so speech in that moment
    # interrupted a finished reply and recorded the turn a second time.
    ws, sink = FakeWS(), _SlowSink()
    session = make_session(
        ws,
        detector=FakeDetector([START, [], END, START]),
        stt=FakeSTT([], "play music"),
        tts=FakeTTS(),
        llm=FakeLLM(["Sure thing.", " Playing it now."]),
        sink=sink,
    )
    await start_call(session, ws)
    await feed(session, 3)
    await asyncio.wait_for(sink.writing.wait(), 5)
    await feed(session, 1)
    await wait_until(lambda: any(e["type"] == "barge_in" for e in events(ws)))
    sink.release.set()
    await wait_until(lambda: len(sink.rows) == 1)
    await asyncio.sleep(0.05)

    assert len(sink.rows) == 1 and sink.rows[0]["barged_in"] is False
    await session._stop_call()


class _SlowFirstDecode:
    """The first utterance's final waits to be released; later ones come straight away."""

    def __init__(self, finals: list[str]) -> None:
        self._finals = list(finals)
        self.release = asyncio.Event()
        self.decoding = asyncio.Event()

    @property
    def pending(self) -> int:
        """Utterances not yet begun."""
        return len(self._finals)

    def stream_transcripts(self, audio: Any) -> Any:
        first = len(self._finals) == 2
        text = self._finals.pop(0)

        async def gen() -> Any:
            async for _ in audio:
                pass
            if first:
                self.decoding.set()
                await self.release.wait()
            yield Transcript(text, is_final=True)

        return gen()


async def test_picking_up_during_a_slow_decode_answers_both_halves_once() -> None:
    """ "Turn on the lights ... and the fan": the second half interrupts while the first
    is still being decoded. The first's reply used to start while the user was still
    talking, and the second got a reply of its own."""
    ws = FakeWS()
    stt = _SlowFirstDecode(["turn on the lights", "and the fan"])
    seen: list[Any] = []
    session = make_session(
        ws,
        detector=FakeDetector([START, [], END, START, [], END]),
        stt=stt,  # type: ignore[arg-type]
        tts=FakeTTS(),
        llm=FakeLLM(["Both on."], seen=seen),
        sink=ListMetricsSink(),
    )
    await start_call(session, ws)
    warmup_calls = len(seen)
    await feed(session, 3)
    await asyncio.wait_for(stt.decoding.wait(), 5)
    await feed(session, 3)  # the second half: an interruption, then its end
    await wait_until(lambda: not stt.pending and session.utterance_q is None)
    stt.release.set()
    await wait_until(lambda: _replies(session) == ["Both on."])

    users = [m["content"] for m in session.history if m["role"] == "user"]
    assert users == ["turn on the lights", "and the fan"]
    assert len(seen) - warmup_calls == 1  # one reply, with both halves in view
    await session._stop_call()


class _HangingGreetingTTS(FakeTTS):
    def synthesize_stream(self, text: str, voice_id: str) -> Any:
        if "Rowan" not in text:
            return super().synthesize_stream(text, voice_id)

        async def gen() -> Any:
            await asyncio.Event().wait()  # a greeting sentence that is taking its time
            yield b""

        return gen()


async def test_talking_over_the_greeting_stops_it_at_once(tmp_path: Path) -> None:
    # The greeting ran inside the warm-up, which an interruption didn't cancel, so it
    # finished its sentence, holding the voice model, while the user talked.
    ws = FakeWS()
    memory = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    await memory.service.set_profile("name", "Rowan")
    session = make_session(
        ws,
        detector=FakeDetector([START]),
        stt=FakeSTT([], None),
        tts=_HangingGreetingTTS(),
        llm=FakeLLM([]),
        sink=ListMetricsSink(),
        memory=memory,
    )
    await session._on_call_start()
    try:
        await wait_until(lambda: states(ws)[-1:] == ["speaking"])
        greeting = session._greeting_task
        assert greeting is not None and not greeting.done()

        await feed(session, 1)
        await wait_until(lambda: session.utterance_q is not None)  # the user has the floor
        await asyncio.sleep(0)  # a cancelled task finishes on its next turn
        assert greeting.cancelled()
    finally:
        await session._stop_call()  # the greeting would otherwise wait forever
        await memory.store.close()


class _SecondSentence(FakeTTS):
    """Makes the first sentence at once; the second fails, or waits to be released."""

    def __init__(self, *, fails: bool) -> None:
        super().__init__()
        self._fails = fails
        self.made = 0
        self.holding = asyncio.Event()
        self.release = asyncio.Event()

    def synthesize_stream(self, text: str, _voice_id: str) -> Any:
        async def gen() -> Any:
            self.made += 1
            if self.made == 2:
                if self._fails:
                    raise RuntimeError("synthesis failed")
                self.holding.set()
                await self.release.wait()
            yield text.encode()

        return gen()


async def test_words_not_yet_played_are_not_kept_when_she_is_cut_off() -> None:
    """Cut off with her first sentence sent and the second being made, before the client
    reported any of it playing. What was made, and then what was sent, went into history;
    nothing is known to have been heard until the client says so."""
    tts = _SecondSentence(fails=False)
    session = make_session(
        FakeWS(),
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=tts,
        llm=FakeLLM(["Paris is the capital of France. ", "It is lovely."]),
        sink=ListMetricsSink(),
    )  # no call started: its warm-up would wait on the held synthesiser
    session.history.append({"role": "user", "content": "what is the capital of france"})
    session.spawn_respond("what is the capital of france")
    await wait_until(tts.holding.is_set)
    assert session.ledger.total_samples > 0  # the first sentence's audio went out

    await session.trigger_barge_in(b"\x00\x00" * 320)
    tts.release.set()

    assert _replies(session) == []
    await session._cancel_active_turn()


async def test_talking_over_the_next_decode_leaves_the_last_reply_as_heard() -> None:
    """The reply before is settled once the next question is asked. Interrupting during
    that question's decode cut it back again, to the client's last report, which comes
    every 100 ms and so falls short of the end: "Hi there." became "Hi…"."""
    stt = _HeldSTT()
    session = make_session(
        FakeWS(),
        detector=FakeDetector([]),
        stt=stt,
        tts=FakeTTS(),
        llm=FakeLLM(["Hi there."]),
        sink=ListMetricsSink(),
    )
    await start_call(session, session.ws)
    session.history.append({"role": "user", "content": "hello"})
    session.spawn_respond("hello")
    await wait_until(lambda: _replies(session) == ["Hi there."] and session._respond_task is None)
    session_control.note_playback(
        session, {"reply": session.reply_id, "samples": session.ledger.total_samples - 1}
    )  # the last report before it finished playing

    frame = b"\x00\x00" * 320
    session.begin_utterance(frame)  # the next question, still being decoded
    session.end_utterance(last_voiced=session.clock())
    await session.trigger_barge_in(frame)

    assert _replies(session) == ["Hi there."]
    stt.release.set()
    await session._stop_call()


async def test_a_reply_that_fails_partway_keeps_what_went_out_and_can_still_be_stopped() -> None:
    """The first sentence was said and is still playing; the second failed to be made.
    History kept the failed sentence too, and the gate closed on the sentence still
    playing, so talking over it no longer stopped her. And when it did, what was heard
    went in as a second copy rather than cutting back the first."""
    session = make_session(
        FakeWS(),
        detector=FakeDetector([]),
        stt=FakeSTT([], None),
        tts=_SecondSentence(fails=True),
        llm=FakeLLM(["The weather is fine. ", "It will rain later."]),
        sink=ListMetricsSink(),
    )
    session.history.append({"role": "user", "content": "how is the weather"})

    await asyncio.wait_for(workers.respond(session, "how is the weather"), timeout=5)

    assert _replies(session) == ["The weather is fine.…"]
    assert session.barge.armed  # still playing: nothing reported it heard
    session_control.note_playback(session, {"reply": session.reply_id, "samples": 5})
    await session.trigger_barge_in(b"\x00\x00" * 320)  # halfway through the sentence
    assert _replies(session) == ["The…"]
    await session._cancel_active_turn()


async def test_after_a_spoken_answer_the_rest_of_the_reply_has_a_mark_of_its_own(
    tmp_path: Path,
) -> None:
    """The user's answer makes the app drop the reply it was playing, position and all,
    so nothing more of that reply was reported: the gate waited out a ceiling counted
    over the whole reply, and history could be cut back to before the question. The
    rest of the reply now comes under its own mark, counted from zero."""
    target = tmp_path / "note.md"
    memory, _kernel, session = await confirm_session(
        tmp_path,
        [{"tokens": ["Sure. "], **_write_call(target)}, {"tokens": ["Done, it is written."]}],
    )
    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    first = session.reply_id
    session_control.note_playback(  # heard up to the question, then the answer
        session, {"reply": first, "samples": session.ledger.total_samples}
    )
    assert await spoken_confirm.answer(session, "yes")
    await asyncio.wait_for(turn, timeout=10)

    queued = []
    while not session.audio_out.empty():
        queued.append(session.audio_out.get_nowait())
    assert [item.reply for item in queued if isinstance(item, ReplyStart)] == [first, first + 1]
    session_control.note_playback(
        session, {"reply": first + 1, "samples": session.ledger.total_samples}
    )
    assert not session.barge.armed  # heard to the end: the rest, reported under its mark
    assert _replies(session)[-1] == "Sure. Done, it is written."
    await memory.store.close()


async def test_a_yes_decoded_after_the_card_was_tapped_starts_no_new_turn(
    tmp_path: Path,
) -> None:
    """The user taps Approve while still saying "yeah, go ahead". The question closes
    before the answer is decoded, and the answer was taken as a new turn: it cancelled
    the reply about the action, which never reached history, and was answered itself."""
    target = tmp_path / "note.md"
    memory, kernel, session = await confirm_session(
        tmp_path, [_write_call(target), {"tokens": ["Done, it is written."]}]
    )
    stt = _HeldSTT("yeah, go ahead")
    session.runtime.stt = stt  # type: ignore[assignment]
    turn = asyncio.create_task(workers.respond(session, "write a note"))
    await _until_asking(session)
    frame = b"\x00\x00" * 320
    session.begin_utterance(frame)  # "yeah, go ahead", begun as the answer
    session.end_utterance(last_voiced=session.clock())
    token = session.awaiting_confirm.token  # type: ignore[union-attr]
    assert kernel.kernel.resolve_confirmation(token, True, via="ui") is True  # the card
    await asyncio.wait_for(turn, timeout=10)

    stt.release.set()  # the answer is decoded only now
    await asyncio.sleep(0.05)

    assert _replies(session)[-1] == "Done, it is written."
    assert session._respond_task is None
    assert len(session.runtime.llm.calls) == 2  # type: ignore[attr-defined]
    await memory.store.close()
