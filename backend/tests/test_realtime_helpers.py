"""Realtime helpers: sentence chunker, throttle, metrics, barge-in gate."""

from __future__ import annotations

from app.services.voice.realtime.barge_in import BargeInGate
from app.services.voice.realtime.chunker import SentenceChunker
from app.services.voice.realtime.metrics import Throttle, TurnMetrics
from app.services.voice.turn import TurnEvent


def test_chunker_emits_complete_sentences() -> None:
    chunker = SentenceChunker()
    assert chunker.push("Hello world. How ") == ["Hello world."]
    # A "?" at the end of what has streamed isn't a boundary yet: more may follow it.
    assert chunker.push("are you?") == []
    assert chunker.flush() == ["How are you?"]
    assert chunker.spoken_text == "Hello world. How are you?"


def test_chunker_keeps_decimals_intact() -> None:
    chunker = SentenceChunker()
    assert chunker.push("Pi is 3.14 exactly. ") == ["Pi is 3.14 exactly."]


def test_chunker_keeps_a_decimal_whole_as_it_streams() -> None:
    # The model sends "3", "." and "30": at the "." the text only looks like a sentence.
    chunker = SentenceChunker()
    assert [chunker.push(token) for token in ["It's 3", ".", "30 now.", " Bye"]] == [
        [],
        [],
        [],
        ["It's 3.30 now."],
    ]
    clauses = SentenceChunker(clause_chunking=True, min_clause_chars=4)
    assert clauses.push("It cost 1") == [] and clauses.push(",") == []
    assert clauses.push("250 pounds, all in") == ["It cost 1,250 pounds,"]


def test_chunker_does_not_end_a_sentence_on_an_abbreviation() -> None:
    chunker = SentenceChunker()
    assert chunker.push("Ask Dr. Smith about it, e.g. on Monday. Then ") == [
        "Ask Dr. Smith about it, e.g. on Monday."
    ]
    # As calls run it, with clause chunking on: "Ask Dr. Smith," is too short a clause.
    clauses = SentenceChunker(clause_chunking=True, min_clause_chars=16)
    assert clauses.push("Ask Dr. Smith, the one downstairs, about it. Then ") == [
        "Ask Dr. Smith, the one downstairs, about it."
    ]


def test_chunker_knows_an_abbreviation_inside_quotes_or_brackets() -> None:
    # "“Dr" wasn't found among the abbreviations, and 'She asked for “Dr.' was spoken
    # as a sentence of its own.
    chunker = SentenceChunker()
    assert chunker.push("She asked for “Dr. Smith” by name, [Mr. Lee] too. Then ") == [
        "She asked for “Dr. Smith” by name, [Mr. Lee] too."
    ]


def test_chunker_keeps_a_long_sentences_last_word() -> None:
    # A full stop that reached the limit waited for its next word, and the limit cut at
    # the last space instead, so "here." went to her voice on its own.
    words = (
        "This sentence runs long without a single comma in it so that nothing but its "
        "own end or the limit can close it and it keeps going on and on for quite a "
        "while yet before it finally stops talking here"
    ).split()
    text = " ".join(words)
    chunker = SentenceChunker(max_chars=len(text) + 1, clause_chunking=True)
    chunks: list[str] = []
    for index, word in enumerate(words):
        chunks += chunker.push(word if index == 0 else f" {word}")
    chunks += chunker.push(".")
    chunks += chunker.push(" Next")
    assert chunks == [f"{text}."]


def test_chunker_max_chars_fallback_flushes_at_space() -> None:
    chunker = SentenceChunker(max_chars=20)
    out = chunker.push("word " * 10)  # 50 chars, no terminal punctuation
    assert out  # forced a chunk before any period
    assert all(len(chunk) <= 20 for chunk in out)


def test_chunker_flush_emits_tail() -> None:
    chunker = SentenceChunker()
    chunker.push("No terminator here")
    assert chunker.flush() == ["No terminator here"]


def test_chunker_clause_mode_flushes_opening_clause_early() -> None:
    chunker = SentenceChunker(clause_chunking=True, min_clause_chars=12)
    # streamed token-by-token: the opening clause flushes before the sentence ends
    assert chunker.push("I'm Veronica, your local") == ["I'm Veronica,"]
    assert chunker.push("-first home assistant. ") == ["your local-first home assistant."]


def test_chunker_clause_mode_respects_min_chars() -> None:
    chunker = SentenceChunker(clause_chunking=True, min_clause_chars=30)
    # "Yes," is too short to flush alone; the whole sentence flushes instead
    assert chunker.push("Yes, absolutely right now. ") == ["Yes, absolutely right now."]


def test_throttle_rate_limits() -> None:
    now = 0.0
    throttle = Throttle(0.2, clock=lambda: now)
    assert throttle.ready() is True  # first call allowed
    assert throttle.ready() is False  # too soon
    now = 0.25
    assert throttle.ready() is True  # interval elapsed


def test_turn_metrics_marks_durations() -> None:
    now = 100.0
    metrics = TurnMetrics(user_chars=5, clock=lambda: now)
    now = 100.2
    metrics.mark_ttft()
    now = 100.9
    metrics.mark_first_audio()
    now = 103.0
    metrics.mark_done(reply_chars=42)

    data = metrics.as_dict()
    assert data["ttft_ms"] == 200
    assert data["first_audio_ms"] == 900
    assert data["total_ms"] == 3000
    assert data["reply_chars"] == 42
    assert data["barged_in"] is False


def test_barge_in_gate_fires_only_when_armed() -> None:
    gate = BargeInGate()
    assert gate.should_interrupt([TurnEvent.speech_start]) is False  # not armed
    gate.arm()
    assert gate.should_interrupt([TurnEvent.speech_end]) is False  # wrong event
    assert gate.should_interrupt([TurnEvent.speech_start]) is True  # armed + start
    assert gate.should_interrupt([TurnEvent.speech_start]) is False  # disarmed after firing
