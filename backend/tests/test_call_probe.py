"""The call probe's own logic: its reading of the metrics row, its percentiles, its
question audio, and how it times a turn. No backend is needed; a fake stands in."""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.services.voice.realtime.metrics import TurnMetrics
from scripts import probe_audio
from scripts.call_probe import Call, ProbeError, reason
from scripts.probe_playback import Speaker
from scripts.probe_stats import ProbeRun, ProbeTurn, TurnRow, report, stats, summarize

SILENCE = bytes(probe_audio.FRAME_BYTES)


def test_the_probe_reads_every_field_the_server_writes() -> None:
    # A stage added to `TurnMetrics` but not here would silently vanish from reports.
    row = TurnMetrics(user_chars=4).as_dict()
    assert set(TurnRow.model_fields) == set(row)
    assert TurnRow.model_validate({"type": "metrics", **row}).user_chars == 4


def test_percentiles_are_turns_that_really_happened() -> None:
    twenty = stats([float(value) for value in range(1, 21)])
    # p95 of 20 turns is the 19th, not the slowest: one outlier must not be the p95.
    assert (twenty.n, twenty.p50, twenty.p90, twenty.p95, twenty.max) == (20, 10.5, 18, 19, 20)
    assert stats([float(value) for value in range(1, 31)]).p90 == 27
    single = stats([5.0])
    assert (single.p50, single.p90, single.p95) == (5, 5, 5)


def _turn(first_audio: int | None, *, tools: int = 0, barged: bool = False) -> ProbeTurn:
    row = TurnMetrics(user_chars=4).as_dict() | {
        "first_audio_ms": first_audio,
        "tool_calls": tools,
        "barged_in": barged,
    }
    return ProbeTurn(
        question="q", heard="q", row=TurnRow.model_validate(row), client_first_audio_ms=None
    )


def test_tool_and_interrupted_turns_are_left_out_of_the_summary() -> None:
    turns = [_turn(1000), _turn(2000), _turn(9000, tools=1), _turn(50, barged=True)]

    summary = summarize(turns)
    assert summary["first_audio_ms"].n == 2 and summary["first_audio_ms"].max == 2000
    assert "chunk_wait_ms" not in summary  # never measured, so not reported as 0
    run = ProbeRun(label="base", version="1.0", started_at="t", turns=turns, loop_lag=None)
    assert "2 left out" in report([run])[0]


def _tone_then_quiet(tone_s: float, quiet_s: float) -> bytes:
    rate = probe_audio.TTS_RATE
    tone = 0.5 * np.sin(2 * np.pi * 440 * np.arange(int(tone_s * rate)) / rate)
    audio = np.concatenate([tone, np.zeros(int(quiet_s * rate))])
    return (audio * 32767).astype("<i2").tobytes()


def test_speech_is_resampled_for_the_microphone_and_its_quiet_tail_cut() -> None:
    audio = probe_audio.to_mic_audio(_tone_then_quiet(0.5, 0.3))

    # Half a second of tone at 16 kHz; the 0.3 s of hush after it is gone.
    assert len(audio) // 2 == 8000
    assert probe_audio.to_mic_audio(bytes(48_000)) == b""  # nothing audible at all


def test_a_cached_clip_round_trips_and_splits_into_microphone_frames(tmp_path: Path) -> None:
    path = probe_audio.clip_path(tmp_path, "What's the capital of France?")
    audio = bytes(range(256)) * 10  # 2560 bytes: four whole frames and a partial one
    probe_audio.save(path, audio)

    assert probe_audio.load(path) == audio
    frames = probe_audio.to_frames(probe_audio.load(path))
    assert len(frames) == 4 and all(len(frame) == probe_audio.FRAME_BYTES for frame in frames)


def test_a_clip_in_another_format_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "wrong.wav"
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(24_000)
        clip.writeframes(bytes(960))

    with pytest.raises(ValueError, match="16 kHz"):
        probe_audio.load(path)


class _FakeServer:
    """Stands in for `/ws/voice`: once the question's audio stops, it answers."""

    def __init__(self, answer: list[dict[str, Any] | bytes | str]) -> None:
        self.sent: list[bytes | str] = []
        self._answer = answer
        self._inbox: asyncio.Queue[bytes | str | None] = asyncio.Queue()
        self._speaking = False

    async def send(self, message: bytes | str) -> None:
        self.sent.append(message)
        if isinstance(message, str):
            return  # a control frame, such as a playback report: not the microphone
        if message != SILENCE:
            self._speaking = True
        elif self._speaking:  # the first silence after speech: the question is over
            self._speaking = False
            for item in self._answer:  # a dict is an event; bytes and str go as they are
                self._inbox.put_nowait(item if isinstance(item, bytes | str) else json.dumps(item))

    def hang_up(self) -> None:
        self._inbox.put_nowait(None)

    def __aiter__(self) -> _FakeServer:
        return self

    async def __anext__(self) -> bytes | str:
        item = await self._inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item


async def _ask(server: _FakeServer, question: list[bytes], timeout_s: float = 5.0) -> ProbeTurn:
    call = Call(server)  # type: ignore[arg-type]
    receiver = asyncio.create_task(call.receive())
    microphone = asyncio.create_task(call.stream_microphone())
    try:
        return await call.ask("Hello?", question, timeout_s)
    finally:
        call.mute()
        await microphone
        server.hang_up()
        await receiver


async def test_a_question_is_timed_from_its_last_frame_to_the_first_reply_audio() -> None:
    row = TurnMetrics(user_chars=6).as_dict()
    server = _FakeServer(
        [
            {"type": "asr_final", "text": "hello"},
            b"\x01\x00" * 480,
            {"type": "metrics", **row},
            {"type": "veronica_state", "state": "listening"},
        ]
    )
    question = [bytes([7]) * probe_audio.FRAME_BYTES] * 3

    turn = await _ask(server, question)

    assert [message for message in server.sent if message != SILENCE] == question
    assert turn.heard == "hello" and turn.row.user_chars == 6
    # The fake answers one frame after the question ends; nothing like a second.
    assert turn.client_first_audio_ms is not None and 0 <= turn.client_first_audio_ms < 1000


def test_the_speaker_plays_in_real_time_and_waits_for_audio_it_lacks() -> None:
    speaker = Speaker()
    speaker.receive(2400, now=0.0)  # before any reply's mark: not counted
    assert speaker.finished(0.0)
    speaker.begin(1)
    speaker.receive(2400, now=10.0)  # 0.1 s of audio
    assert speaker.played(10.05) == 1200
    assert speaker.played(11.0) == 2400  # ran out: it waits
    speaker.receive(2400, now=11.0)
    assert speaker.played(11.05) == 3600  # and plays on from when more came
    assert not speaker.finished(11.05)
    assert speaker.finished(11.2)


async def test_the_probe_lets_a_reply_play_out_and_says_how_far_it_got() -> None:
    # A probe that asked again as soon as the reply was made would talk over it, and
    # the server would take every question for an interruption.
    row = TurnMetrics(user_chars=6).as_dict()
    server = _FakeServer(
        [
            {"type": "asr_final", "text": "hello"},
            {"type": "reply_audio", "reply": 4},
            b"\x01\x00" * 7200,  # 0.3 s of her voice
            {"type": "metrics", **row},
            {"type": "veronica_state", "state": "listening"},
        ]
    )
    started = asyncio.get_running_loop().time()
    await _ask(server, [bytes([7]) * probe_audio.FRAME_BYTES])

    assert asyncio.get_running_loop().time() - started >= 0.3
    reports = [json.loads(m) for m in server.sent if isinstance(m, str)]
    assert reports and all(r["action"] == "playback_position" and r["reply"] == 4 for r in reports)
    samples = [r["samples"] for r in reports]
    assert samples == sorted(samples) and samples[-1] <= 7200


async def test_a_question_heard_as_two_utterances_is_refused_not_mislabelled() -> None:
    # A pause inside the recording: the second half interrupts the reply to the first.
    # Counting it would credit one question's reply to the next.
    row = TurnMetrics(user_chars=6).as_dict()
    server = _FakeServer(
        [
            {"type": "asr_final", "text": "what's the"},
            {"type": "metrics", **row, "barged_in": True},
            {"type": "asr_final", "text": "capital of France"},
            {"type": "metrics", **row},
            {"type": "veronica_state", "state": "listening"},
        ]
    )

    with pytest.raises(ProbeError, match="heard as 2 utterances"):
        await _ask(server, [bytes([7]) * probe_audio.FRAME_BYTES] * 2)


async def test_an_interrupted_reply_is_refused() -> None:
    row = TurnMetrics(user_chars=6).as_dict() | {"barged_in": True}
    server = _FakeServer(
        [
            {"type": "asr_final", "text": "hello"},
            {"type": "metrics", **row},
            {"type": "veronica_state", "state": "listening"},
        ]
    )

    with pytest.raises(ProbeError, match="interrupted"):
        await _ask(server, [bytes([7]) * probe_audio.FRAME_BYTES])


async def test_a_server_error_stops_the_probe_and_names_it() -> None:
    server = _FakeServer([{"type": "error", "kind": "stt_failed", "message": "boom"}])

    with pytest.raises(ProbeError, match="stt_failed"):
        await _ask(server, [bytes([7]) * probe_audio.FRAME_BYTES])


async def test_a_message_that_isnt_json_is_named_as_such() -> None:
    server = _FakeServer(["<html>not a call</html>"])

    with pytest.raises(ProbeError, match="isn't JSON"):
        await _ask(server, [bytes([7]) * probe_audio.FRAME_BYTES])


async def test_a_turn_that_never_ends_times_out_with_the_last_state() -> None:
    server = _FakeServer([{"type": "veronica_state", "state": "thinking"}])

    with pytest.raises(ProbeError, match="thinking"):
        await _ask(server, [bytes([7]) * probe_audio.FRAME_BYTES], timeout_s=0.3)


def test_a_failure_without_a_message_still_says_what_it_was() -> None:
    # asyncio's timeout raises a bare TimeoutError; printing it gave "Probe failed: ".
    assert reason(TimeoutError()) == "TimeoutError"
    assert reason(ValueError("bad clip")) == "bad clip"
