"""The barge-in gate stays open while her reply plays, not only while it is made."""

from __future__ import annotations

from app.services.voice.realtime.barge_in import BargeInGate
from app.services.voice.turn import TurnEvent

SPEECH = [TurnEvent.speech_start]


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _playing(total_samples: int = 48_000) -> tuple[BargeInGate, _Clock]:
    """A gate whose reply is fully made (2 s of audio at 24 kHz) and just began to play."""
    clock = _Clock()
    gate = BargeInGate(clock)
    gate.arm()
    gate.playback_started()
    gate.synthesis_done(total_samples)
    return gate, clock


def test_it_stays_open_while_a_finished_reply_is_still_playing() -> None:
    # It used to close when the last sentence was made, with seconds of audio to go.
    gate, clock = _playing()
    clock.now += 1.0  # halfway through two seconds of audio
    assert gate.armed and gate.playing
    assert gate.should_interrupt(SPEECH)
    assert not gate.armed  # fired once


def test_it_closes_once_the_client_has_played_it_all() -> None:
    gate, clock = _playing()
    gate.note_played(24_000)
    assert gate.armed
    gate.note_played(48_000)
    assert not gate.armed and not gate.playing
    assert not gate.should_interrupt(SPEECH)  # speech now is a new turn


def test_without_reports_it_closes_when_the_audio_must_have_ended() -> None:
    gate, clock = _playing()
    clock.now += 2.0 + 0.4  # the audio's length, inside the margin
    assert gate.armed
    clock.now += 0.2
    assert not gate.armed


def test_reports_keep_it_open_through_a_pause_in_her_speech() -> None:
    # The next sentence still being made stalls playback past the audio's own length;
    # the client, still reporting, is the better judge.
    gate, clock = _playing()
    clock.now += 2.4
    gate.note_played(40_000)
    clock.now += 0.4  # past the ceiling, but a report came 0.4 s ago
    assert gate.armed
    clock.now += 0.2
    assert not gate.armed


def test_a_position_never_goes_back() -> None:
    gate, _clock = _playing()
    gate.note_played(48_000)
    gate.note_played(1_000)  # stale, arriving late
    assert not gate.armed


def test_it_is_open_but_not_playing_until_her_audio_goes_out() -> None:
    gate = BargeInGate(_Clock())
    gate.arm()
    assert gate.armed and not gate.playing  # thinking: interruptible, nothing heard
    gate.playback_started()
    assert gate.playing


def test_a_reply_that_made_no_audio_leaves_nothing_to_interrupt() -> None:
    gate, _clock = _playing(total_samples=0)
    assert not gate.armed


def test_arming_again_forgets_the_last_reply() -> None:
    gate, clock = _playing()
    gate.note_played(48_000)
    gate.arm()
    assert gate.armed and not gate.playing  # a new reply, nothing of it played yet
    gate.disarm()
    assert not gate.armed
