"""The speaking-rate fit behind the greeting's length cap (`scripts/speech_rate.py`)."""

from __future__ import annotations

import pytest

from scripts.speech_rate import Reading, fit, seconds_of


def test_the_fit_recovers_a_known_rate() -> None:
    readings = [Reading(chars, 0.5 + 0.05 * chars) for chars in (10, 40, 90, 160)]

    fixed, per_char = fit(readings)

    assert fixed == pytest.approx(0.5) and per_char == pytest.approx(0.05)


def test_readings_of_one_length_cannot_be_fitted() -> None:
    with pytest.raises(ValueError, match="two different lengths"):
        fit([Reading(40, 2.0), Reading(40, 2.4)])


def test_a_second_of_the_voice_is_a_second() -> None:
    assert seconds_of(bytes(2 * 24_000)) == 1.0  # 24 kHz, two bytes a sample
