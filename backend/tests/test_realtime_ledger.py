"""The spoken ledger: from how far the client has played to the words that were heard."""

from __future__ import annotations

from app.services.voice.realtime.ledger import SpokenLedger


def _ledger(*chunks: tuple[str, int]) -> SpokenLedger:
    ledger = SpokenLedger()
    for text, samples in chunks:
        ledger.begin(text)
        ledger.extend(samples)
    return ledger


def test_nothing_played_is_nothing_heard() -> None:
    assert _ledger(("Paris is the capital.", 100)).text_up_to(0) == ""


def test_whole_chunks_come_back_whole() -> None:
    ledger = _ledger(("Paris is the capital.", 100), ("It sits on the Seine.", 100))
    assert ledger.text_up_to(100) == "Paris is the capital."
    assert ledger.text_up_to(200) == "Paris is the capital. It sits on the Seine."
    assert ledger.text_up_to(10_000) == ledger.text_up_to(200)  # never more than was made
    assert ledger.total_samples == 200


def test_a_chunk_cut_short_keeps_its_whole_words_and_says_so() -> None:
    ledger = _ledger(("Paris is the capital.", 100), ("It sits on the Seine.", 100))
    # Half of the second chunk: "It sits on" is 10 of its 21 characters.
    assert ledger.text_up_to(150) == "Paris is the capital. It sits on…"


def test_a_cut_inside_the_first_word_claims_nothing() -> None:
    ledger = _ledger(("Wonderful news today.", 100))
    assert ledger.text_up_to(10) == ""  # "Wo" is not a word she said


def test_a_cut_on_a_space_keeps_the_word_before_it() -> None:
    # "of France" once came back as "of": the cut landed on the space, then was trimmed
    # back a word it didn't need to be.
    ledger = _ledger(("Paris of France", 150))
    assert ledger.text_up_to(90) == "Paris of…"


def test_audio_without_words_counts_but_adds_nothing() -> None:
    # A spoken question is in the audio the client plays, and not part of the reply.
    ledger = _ledger(("Sure.", 50), ("", 100), ("Done, it's written.", 50))
    assert ledger.text_up_to(150) == "Sure."
    assert ledger.text_up_to(200) == "Sure. Done, it's written."


def test_a_chunk_that_made_no_audio_was_never_heard() -> None:
    ledger = _ledger(("Sure.", 50), ("Never synthesised.", 0), ("Next.", 50))
    assert ledger.text_up_to(100) == "Sure. Next."


def test_her_words_end_where_the_last_of_them_does() -> None:
    """Audio after her words (a question, a caveat) isn't the reply. A ledger holding no
    words at all, as after a spoken confirmation, ends at its start: talking over a
    caveat then must not mark the reply as cut off (mutation: count all its audio)."""
    assert _ledger(("Sure, done.", 100), ("", 60)).words_end == 100
    assert _ledger(("", 60)).words_end == 0


def test_reset_starts_the_next_reply_from_nothing() -> None:
    ledger = _ledger(("Old reply.", 100))
    ledger.reset()
    assert ledger.total_samples == 0
    assert ledger.text_up_to(100) == ""


def test_a_laugh_takes_time_but_is_never_heard_as_words() -> None:
    # Cut while the laugh that opens the chunk plays: no word of it was said yet.
    ledger = SpokenLedger()
    ledger.begin("[laugh] Okay, that one got me.")
    ledger.extend(3000)
    assert ledger.text_up_to(900) == ""
    assert ledger.text_up_to(3000) == "Okay, that one got me."
    assert ledger.words_end == 3000


def test_a_chunk_that_is_only_a_sound_holds_no_words() -> None:
    ledger = SpokenLedger()
    ledger.begin("Good one.")
    ledger.extend(1000)
    ledger.begin("[laugh]")
    ledger.extend(800)
    assert ledger.words_end == 1000  # heard whole once the words are, laugh or not
    assert ledger.text_up_to(1800) == "Good one."
