"""Expression tags: her voice gets the sounds she may make, and nothing shown or kept
ever holds a tag or a stage direction (`adapters/tts/expression.py`)."""

from __future__ import annotations

import random

import pytest

from app.adapters.tts.expression import (
    SPOKEN_TAGS,
    ExpressionFilter,
    Split,
    classify,
    considerate,
    without_tags,
)
from app.services.persona.defaults import EXPRESSION_RULES, with_expression
from app.services.voice.realtime.workers import build_messages


def stream(chunks: list[str], *, expressive: bool = True) -> Split:
    """Feed `chunks` as a model streams them, then end; everything each reader got."""
    voice = ExpressionFilter(expressive=expressive)
    parts = [voice.feed(chunk) for chunk in chunks] + [voice.end()]
    return Split("".join(p.shown for p in parts), "".join(p.spoken for p in parts))


@pytest.mark.parametrize(
    ("piece", "expected"),
    [
        ("[laugh]", "laugh"),
        ("[Laugh]", "laugh"),
        ("[ sighs ]", "sigh"),
        ("*laughs*", "laugh"),
        ("*laughs softly*", "laugh"),
        ("(chuckles)", "chuckle"),
        ("*giggles*", "chuckle"),
        ("[gasp]", "gasp"),
        ("*Laughs.*", "laugh"),  # a direction's own punctuation
        ("(Sighs.)", "sigh"),
        ("(laughs, shaking head)", "laugh"),
        ("*chuckles!*", "chuckle"),
        ("*she laughs*", "laugh"),  # the one doing it, first
        ("(she\u2019s laughing)", "laugh"),
        ("(sadly, no)", None),  # an aside, not a direction
        ("(chuckling uncontrollably and helplessly)", None),  # longer than is held
        ("[smiles]", ""),  # a direction, but not a sound she makes
        ("[happy]", ""),
        ("[clears throat]", ""),
        ("*nods*", ""),
        ("[1]", None),  # a number: ordinary text
        ("[TOOL_CALLS]", None),
        ("*really*", None),  # emphasis, not a direction
        ("(see above)", None),
        ("(5 minutes)", None),
    ],
)
def test_what_a_bracketed_piece_is(piece: str, expected: str | None) -> None:
    assert classify(piece) == expected


def test_a_tag_is_heard_and_never_shown() -> None:
    out = stream(["[laugh] Okay, that one got me."])
    assert out.shown == "Okay, that one got me."
    assert out.spoken == "[laugh] Okay, that one got me."


def test_a_tag_split_across_tokens_never_shows_half() -> None:
    voice = ExpressionFilter(expressive=True)
    first = voice.feed("Ha. [la")
    assert first.shown == "Ha."
    assert "[" not in first.spoken  # held back with the rest of the tag
    second = voice.feed("ugh] Good one.")
    assert second.shown == " Good one."
    assert second.spoken == "[laugh] Good one."


def test_a_directions_own_words_become_the_sound() -> None:
    assert stream(["*laughs* Sure."]).spoken == "[laugh] Sure."
    assert stream(["That's it (sighs)."]).spoken == "That's it [sigh]."
    assert stream(["[Chuckle] Fine."]).spoken == "[chuckle] Fine."


def test_sounds_she_may_not_make_are_dropped_from_her_voice_too() -> None:
    # [gasp] was performed in 2 takes of 12; [dramatic] once spoke the reference clip.
    for tag in ("gasp", "dramatic", "happy", "whispering", "smiles"):
        out = stream([f"[{tag}] Hello there."])
        assert out.shown == "Hello there."
        assert out.spoken == " Hello there."


def test_switched_off_her_voice_makes_no_sound() -> None:
    out = stream(["[laugh] Okay. *sighs* Fine."], expressive=False)
    assert "[" not in out.spoken and "*" not in out.spoken
    assert out.shown == "Okay. Fine."


@pytest.mark.parametrize(
    ("chunks", "shown"),
    [
        (["Well, [chuckle]", " okay."], "Well, okay."),
        (["That is it (sighs)."], "That is it."),
        (["Done. ", "[laugh]"], "Done."),
        (["[smiles]", " ", "Hi"], "Hi"),
        (["Okay [laugh], fine."], "Okay, fine."),
        (["Ha[laugh] ha."], "Ha ha."),
        (["[laugh]. Okay."], "Okay."),  # nothing before it: its punctuation goes too
        (["*laughs*! That's great."], "That's great."),
        (["No tags at all, just words."], "No tags at all, just words."),
    ],
)
def test_taking_a_tag_out_leaves_tidy_spacing(chunks: list[str], shown: str) -> None:
    assert stream(chunks).shown == shown


def test_ordinary_brackets_and_asterisks_pass_through() -> None:
    text = "See [1], it's *really* good: 5 * 3 = 15 (or so)."
    assert stream([text]) == Split(text, text)


def test_an_opener_that_never_closes_is_released() -> None:
    voice = ExpressionFilter(expressive=True)
    assert voice.feed("It costs (about").shown == "It costs"
    assert voice.end().shown == " (about"


def test_a_long_bracket_is_ordinary_text_without_waiting_for_the_end() -> None:
    voice = ExpressionFilter(expressive=True)
    aside = "(this aside runs on well past any stage direction there is"
    assert voice.feed(aside).shown == aside


def test_without_tags_is_what_is_shown_and_remembered() -> None:
    assert without_tags("[laugh] Okay. *sighs* That got me [1].") == "Okay. That got me [1]."
    assert without_tags("[laugh]") == ""
    assert without_tags("It costs (about") == "It costs (about"  # what was held, kept


def test_a_tag_inside_ordinary_brackets_is_still_found() -> None:
    out = stream(["Okay (well, [laugh] fine) and *sure, (sighs) why not*."])
    assert out.shown == "Okay (well, fine) and *sure, why not*."
    assert out.spoken == "Okay (well, [laugh] fine) and *sure, [sigh] why not*."


def test_an_asterisk_in_sums_holds_back_no_more_than_a_short_piece() -> None:
    voice = ExpressionFilter(expressive=True)
    shown = voice.feed("Five * three is fifteen, and that is all there is to say about it.")
    assert shown.shown == "Five * three is fifteen, and that is all there is to say about it."


def test_the_rule_names_exactly_the_sounds_she_may_make() -> None:
    for tag in SPOKEN_TAGS:
        assert f"[{tag}]" in EXPRESSION_RULES
    assert "[gasp]" not in EXPRESSION_RULES
    assert with_expression("Prompt.", False) == "Prompt."
    assert with_expression("Prompt.", True) == f"Prompt.\n\n{EXPRESSION_RULES}"


def test_a_calls_prompt_carries_the_rule_only_while_expressive() -> None:
    on = build_messages("Persona.", "Memory.", [], 8, expressive=True)[0]["content"]
    off = build_messages("Persona.", "Memory.", [], 8)[0]["content"]
    assert on == f"Persona.\n\n{EXPRESSION_RULES}\n\nMemory."
    assert off == "Persona.\n\nMemory."


@pytest.mark.parametrize(
    ("sentence", "said"),
    [
        ("[laugh] That's rough. ", "That's rough. "),
        ("[chuckle] Oh no, I'm so sorry.", "Oh no, I'm so sorry."),
        ("[laugh] You're safe now.", "You're safe now."),
        ("[laugh] Good one!", "[laugh] Good one!"),
        ("[laugh] Unsafe? Lossy!", "[laugh] Unsafe? Lossy!"),  # whole words only
        ("[sigh] Sorry, that's a pain.", "[sigh] Sorry, that's a pain."),  # sighs may
        ("[sigh]", ""),  # alone, the voice said a word for it
        (" [sigh] ", ""),
        ("[laugh]", "[laugh]"),  # alone, it laughed
    ],
)
def test_no_laugh_in_a_sentence_that_sympathises(sentence: str, said: str) -> None:
    assert considerate(sentence) == said


_REPLIES = [
    "Ha. (chuckling uncontrollably and helplessly) Yes. *Laughs.* (she\u2019s laughing)",
    "[laugh] Okay, that one got me. *sighs* Fine (well, [chuckle] mostly).",
    "See [1], it's *really* good: 5 * 3 = 15 (or so) [smiles] and [gasp] done.",
    "Ha[laugh] ha. [sigh] Sorry, that's a pain, [laugh] honestly (sighs).",
    "Open ( and never closed, [laugh] then * alone and [unclosed",
]


@pytest.mark.parametrize("text", _REPLIES)
@pytest.mark.parametrize("expressive", [True, False])
def test_however_the_text_arrives_the_result_is_the_same(text: str, expressive: bool) -> None:
    """A model's tokens split a reply anywhere; 500 random splits each give what one
    piece gives."""
    whole = stream([text], expressive=expressive)
    rng = random.Random(f"{text}{expressive}")
    for _ in range(500):
        cuts = sorted(rng.sample(range(1, len(text)), rng.randint(1, 12)))
        chunks = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)], strict=True)]
        assert stream(chunks, expressive=expressive) == whole, chunks
    assert whole.shown == without_tags(text)


def test_a_space_a_model_call_ended_on_isnt_doubled_by_the_next() -> None:
    voice = ExpressionFilter(expressive=True)
    shown = voice.feed("Let me check. ").shown + voice.end().shown
    shown += voice.feed(" It's sunny.").shown + voice.end().shown  # the next call's text
    assert shown == "Let me check. It's sunny."


@pytest.mark.parametrize(
    ("before", "said"),
    [
        ("Oh no, that's awful. [laugh]", ""),  # a laugh with no words goes with them
        ("Why did the chicken cross? To get across. [laugh]", "[laugh]"),
        ("", "[laugh]"),
    ],
)
def test_a_laugh_on_its_own_goes_with_the_sentence_before_it(before: str, said: str) -> None:
    assert considerate("[laugh]", before) == said
