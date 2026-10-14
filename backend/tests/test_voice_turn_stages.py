"""Per-stage timing of a voice turn, from the user's last word to the first audio.

The stages must add up to the perceived latency, so a slow turn can be pinned on
one stage instead of guessed at.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.adapters.llm.types import LlmChunk
from app.core.config import VadSettings, get_settings
from app.core.event_bus import InProcessEventBus
from app.services.voice.audio import Transcript
from app.services.voice.realtime import session_emit, spoken_confirm, workers
from app.services.voice.realtime.metrics import TurnMetrics, UtteranceMarks
from app.services.voice.realtime.session import RealtimeSession
from app.services.voice.turn import SileroTurnDetector, TurnEvent
from app.telemetry import install, uninstall
from app.telemetry.blackbox import BlackBox
from app.telemetry.perf import PerfLogger
from app.telemetry.runctx import RunContext
from tests.fakes import (
    FakeDetector,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    Warms,
    build_fake_runtime,
)
from tests.test_realtime_session import feed, start_call, wait_until
from tests.test_vad import _FakeVad
from tests.test_voice_confirm import _session as confirm_session
from tests.test_voice_confirm import _write_call
from tests.test_voice_tools import _read_call
from tests.test_voice_tools import _session as tools_session


class _Clock:
    """A clock that only moves when a test (or a fake model) moves it."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _timed_turn() -> dict[str, Any]:
    """A turn with every mark at a known time."""
    clock = _Clock(10.0)
    heard = UtteranceMarks()
    heard.mark_endpoint(10.416, last_voiced=10.0)
    heard.stt_final = 10.600
    clock.now = 10.602
    metrics = TurnMetrics(user_chars=12, clock=clock, utterance=heard)
    metrics.prefetch_s = 0.040
    clock.now = 11.000
    metrics.mark_ttft()
    clock.now = 11.250
    metrics.mark_first_chunk()
    clock.now = 11.700
    metrics.mark_first_audio()
    clock.now = 13.000
    metrics.mark_done(reply_chars=80)
    return metrics.as_dict()


def test_the_stages_add_up_to_the_perceived_latency() -> None:
    row = _timed_turn()

    assert row["endpoint_wait_ms"] == 416
    assert row["stt_final_ms"] == 184
    assert row["handoff_ms"] == 2
    assert row["prefetch_ms"] == 40
    assert row["ttft_ms"] == 398
    assert row["chunk_wait_ms"] == 250
    assert row["tts_first_synth_ms"] == 450
    assert row["first_audio_ms"] == 1098
    assert row["perceived_first_audio_ms"] == 1700
    assert row["total_ms"] == 2398
    before_respond = row["endpoint_wait_ms"] + row["stt_final_ms"] + row["handoff_ms"]
    assert row["perceived_first_audio_ms"] == before_respond + row["first_audio_ms"]
    reply = row["ttft_ms"] + row["chunk_wait_ms"] + row["tts_first_synth_ms"]
    assert row["first_audio_ms"] == reply


def test_a_turn_without_utterance_marks_reports_only_its_own_stages() -> None:
    clock = _Clock(5.0)
    metrics = TurnMetrics(user_chars=3, clock=clock)
    clock.now = 5.3
    metrics.mark_ttft()
    metrics.mark_first_chunk()
    clock.now = 5.5
    metrics.mark_first_audio()
    row = metrics.as_dict()

    assert row["first_audio_ms"] == 500
    for missing in ("endpoint_wait_ms", "stt_final_ms", "handoff_ms", "perceived_first_audio_ms"):
        assert row[missing] is None, missing  # never a made-up 0


def test_an_acknowledgement_before_the_models_first_word_has_no_chunk_wait() -> None:
    # A slow tool: "One moment." goes to TTS before the model has written anything.
    clock = _Clock(1.0)
    metrics = TurnMetrics(user_chars=10, clock=clock)
    clock.now = 1.7
    metrics.mark_first_chunk()
    clock.now = 2.0
    metrics.mark_first_audio()
    clock.now = 2.4
    metrics.mark_ttft()
    row = metrics.as_dict()

    assert row["chunk_wait_ms"] is None  # not a negative wait
    assert row["tts_first_synth_ms"] == 300  # the acknowledgement's synthesis is real


def _detector(probs: list[float]) -> SileroTurnDetector:
    # No turn model and a window of one width: speech ends at 96 ms of silence.
    settings = VadSettings(
        min_speech_ms=64, end_silence_min_ms=96, end_silence_max_ms=96, threshold=0.5
    )
    return SileroTurnDetector(settings, _FakeVad(probs))


def test_the_detector_dates_the_last_word_from_the_silence_after_it() -> None:
    # 32 ms windows, fed as the 20 ms frames a microphone sends: three voiced windows,
    # then 96 ms of silence ends the speech with 128 samples (8 ms) not yet scored.
    detector = _detector([0.9, 0.9, 0.9, 0.1, 0.1, 0.1])
    frame = np.zeros(320, dtype=np.float32)

    for _ in range(20):
        if TurnEvent.speech_end in detector.process_audio(frame):
            break
    else:
        pytest.fail("the speech never ended")

    assert detector.trailing_silence_ms() == pytest.approx(104.0)


def test_a_long_frame_does_not_hide_audio_after_the_end_of_speech() -> None:
    # One frame of eight windows and 128 samples. Speech ends on the sixth window; the
    # two windows scored after it in the same call came after the last word too.
    detector = _detector([0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1])

    events = detector.process_audio(np.zeros(8 * 512 + 128, dtype=np.float32))

    assert TurnEvent.speech_end in events
    assert detector.trailing_silence_ms() == pytest.approx(96 + 64 + 8)


class _TimedSTT(FakeSTT):
    """A partial after 100 ms of decoding, the final transcript 80 ms later."""

    def __init__(self, clock: _Clock) -> None:
        super().__init__([], None)
        self._clock = clock

    def stream_transcripts(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        async def gen() -> AsyncIterator[Transcript]:
            async for _ in audio:
                pass
            self._clock.now += 0.100
            yield Transcript("how", is_final=False)
            self._clock.now += 0.080
            yield Transcript("how are you", is_final=True)

        return gen()


class _TimedLLM(Warms):
    """First token after 300 ms; the first sentence is complete 250 ms later.

    Each token waits on the loop before it arrives, as a real stream waits on the
    network. The reply is read ahead of the speaker (`reply_cap.read_ahead`), and a fake
    that never yields would be read to its end, clock and all, before the first token
    was marked.
    """

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    async def stream_chat(
        self, messages: Any, tools: Any = None, *, model: Any = None, keep_alive: Any = None
    ) -> AsyncIterator[LlmChunk]:
        await asyncio.sleep(0)
        self._clock.now += 0.300
        yield LlmChunk(content="Fine")
        await asyncio.sleep(0)
        self._clock.now += 0.250
        yield LlmChunk(content=", thanks. ")
        yield LlmChunk(done=True)

    async def aclose(self) -> None:
        return None


class _TimedTTS(FakeTTS):
    """Each sentence's audio arrives 450 ms after it is handed over."""

    def __init__(self, clock: _Clock) -> None:
        super().__init__()
        self._clock = clock

    def synthesize_stream(self, text: str, _voice_id: str) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            self._clock.now += 0.450
            yield text.encode()

        return gen()


async def test_each_stage_is_stamped_where_it_happens() -> None:
    # The fakes move the session's clock by known amounts at known points, so a mark
    # taken in the wrong place shows up as a wrong number, not a rounding error.
    clock = _Clock(100.0)
    ws, sink = FakeWS(), ListMetricsSink()
    session = RealtimeSession(
        ws,  # type: ignore[arg-type]
        build_fake_runtime(stt=_TimedSTT(clock), tts=_TimedTTS(clock), llm=_TimedLLM(clock)),  # type: ignore[arg-type]
        InProcessEventBus(),
        get_settings(),
        persona=lambda: "You are Veronica.",
        metrics_sink=sink,  # type: ignore[arg-type]
        detector=FakeDetector(
            [[TurnEvent.speech_start], [], [TurnEvent.speech_end]], silence_ms=416
        ),
        clock=clock,
    )
    await start_call(session, ws)
    await feed(session, 3)
    await wait_until(lambda: len(sink.rows) == 1)
    await session._stop_call()
    [row] = sink.rows

    expected = {
        "endpoint_wait_ms": 416,
        "stt_final_ms": 180,  # the final transcript, not the partial before it
        "handoff_ms": 0,
        "prefetch_ms": 0,
        "ttft_ms": 300,
        "chunk_wait_ms": 250,  # "Fine," alone is too short to send; the sentence goes
        "tts_first_synth_ms": 450,
        "first_audio_ms": 1000,
        "perceived_first_audio_ms": 1596,
        "total_ms": 1000,
        "tool_calls": 0,
    }
    assert {key: row[key] for key in expected} == expected


async def test_a_tool_turn_says_how_many_tools_it_ran(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_text("the roof needs fixing", encoding="utf-8")
    _ws, (memory, _kernel), session = await tools_session(
        tmp_path, [_read_call(note), {"tokens": ["It says the roof needs fixing."]}]
    )
    try:
        await workers.respond(session, "what does my note say?")

        [row] = session._metrics_sink.rows  # type: ignore[attr-defined]
        assert row["tool_calls"] == 1
    finally:
        await memory.store.close()  # an open store keeps the test process alive


async def test_a_spoken_confirmation_is_the_first_audio_of_its_turn(tmp_path: Path) -> None:
    # "Want me to?" is the first thing heard. If only the reply after the answer
    # counted, the turn's timings would include however long the user took to answer.
    target = tmp_path / "note.md"
    memory, _kernel, session = await confirm_session(
        tmp_path, [_write_call(target), {"tokens": ["Done, it's written."]}]
    )
    turn = asyncio.create_task(workers.respond(session, "write a note"))
    try:
        # The question has been spoken once its audio is queued.
        await wait_until(
            lambda: session.awaiting_confirm is not None and not session.audio_out.empty()
        )
        metrics = session.current_metrics
        assert metrics is not None
        heard_at = metrics.as_dict()["first_audio_ms"]
        assert heard_at is not None
        await asyncio.sleep(0.2)  # the user takes a moment to answer
        await spoken_confirm.answer(session, "yes")
        await asyncio.wait_for(turn, timeout=10)

        [row] = session._metrics_sink.rows  # type: ignore[attr-defined]
        assert row["first_audio_ms"] == heard_at
        assert row["tool_calls"] == 1
    finally:
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await memory.store.close()


def test_every_stage_reaches_the_performance_log(tmp_path: Path) -> None:
    run = RunContext.create(app_version="9.9.9", channel="dev")
    box = BlackBox(run, tmp_path, queue_size=100, runs_kept=5, max_run_bytes=1_000_000, clip=500)
    perf = PerfLogger(run, tmp_path / "perf", max_bytes=1_000_000, files_kept=2)
    install(perf, box)
    try:
        session_emit._log_turn_perf(_timed_turn())
    finally:
        uninstall()
        perf.close()
        box.close()
    lines = (tmp_path / "perf" / "perf.jsonl").read_text(encoding="utf-8").splitlines()
    logged = {row["stage"]: row for row in map(json.loads, lines)}

    assert set(logged) == {stage for stage, _key in session_emit._TURN_STAGES}
    assert logged["voice.perceived_first_audio"]["dur_ms"] == 1700
    assert logged["voice.stt_final"]["meta"]["tool_calls"] == 0
