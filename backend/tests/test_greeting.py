"""The varied speaks-first greeting builder (deterministic, zero-LLM)."""

from __future__ import annotations

from datetime import datetime

from app.services.memory.models import AddressByName
from app.services.voice.realtime.greeting import OPENER_MAX_CHARS, GreetingOpts, TemplateGreeter

GREETER = TemplateGreeter()
MORNING = datetime(2026, 6, 26, 9, 0, 0)  # also a time the "Last time" callback fires
EVENING = datetime(2026, 6, 26, 20, 0, 0)
LONG_RECAP = (
    "we went through your notes for Thursday's talk, cut the introduction down, and "
    "rewrote the ending twice."
)


def test_first_meeting_when_no_name() -> None:
    text = GREETER.build(name=None, summary=None, now=MORNING, opts=GreetingOpts())
    assert "Veronica" in text and "?" in text  # introduces herself, asks the name


def test_returning_uses_name_and_time_of_day() -> None:
    text = GREETER.build(name="Rowen", summary=None, now=MORNING, opts=GreetingOpts())
    assert "Rowen" in text
    assert any(word in text for word in ("Morning", "Good morning", "Welcome back"))


def test_callback_included_for_some_times_not_others() -> None:
    # _occasional is seeded by the time; at least one second in a minute triggers it.
    summary = "we were baking sourdough"
    hits = [
        GREETER.build(
            name="Rowen",
            summary=summary,
            now=datetime(2026, 6, 26, 9, 0, second),
            opts=GreetingOpts(),
        )
        for second in range(6)
    ]
    assert any(summary in text for text in hits)  # callback fires sometimes
    assert any(summary not in text for text in hits)  # and not every time (it's occasional)


def test_brief_drops_the_callback() -> None:
    text = GREETER.build(
        name="Rowen",
        summary="we baked sourdough",
        now=MORNING,
        opts=GreetingOpts(length="brief"),
    )
    assert "Last time" not in text and "Rowen" in text


def test_disabled_is_empty() -> None:
    assert (
        GREETER.build(name="Rowen", summary=None, now=MORNING, opts=GreetingOpts(enabled=False))
        == ""
    )


def test_never_greets_a_known_user_without_their_name() -> None:
    # The opener is where the name is most likely to be used, so `never` must hold there
    # too, and the greeting must not ask for the name instead.
    text = GREETER.build(
        name="Rowen", summary=None, now=EVENING, opts=GreetingOpts(), address=AddressByName.never
    )
    assert (
        text and "Rowen" not in text and "?" not in text
    )  # warm, name-less, no "what's your name"


def test_deterministic_for_a_fixed_time() -> None:
    a = GREETER.build(name="Rowen", summary=None, now=EVENING, opts=GreetingOpts())
    b = GREETER.build(name="Rowen", summary=None, now=EVENING, opts=GreetingOpts())
    assert a == b  # reproducible given the same clock


def test_a_recap_that_fits_is_said() -> None:
    text = GREETER.build(
        name="Rowen", summary="we were planning the garden.", now=MORNING, opts=GreetingOpts()
    )
    assert text.endswith(" Last time, we were planning the garden.")
    assert len(text) <= OPENER_MAX_CHARS


def test_a_recap_that_would_run_the_opener_long_is_left_out_whole() -> None:
    # The summary is a sentence the model wrote, with no length of its own. Past about six
    # seconds the opener is a monologue; the recap goes, and never half of it.
    text = GREETER.build(name="Rowen", summary=LONG_RECAP, now=MORNING, opts=GreetingOpts())
    assert text == GREETER.build(name="Rowen", summary=None, now=MORNING, opts=GreetingOpts())


def test_the_recap_is_kept_up_to_the_last_character_that_fits() -> None:
    greeting = GREETER.build(name="Rowen", summary=None, now=MORNING, opts=GreetingOpts())
    room = OPENER_MAX_CHARS - len(f"{greeting} Last time, ")
    exact = GREETER.build(name="Rowen", summary="x" * room, now=MORNING, opts=GreetingOpts())
    over = GREETER.build(name="Rowen", summary="x" * (room + 1), now=MORNING, opts=GreetingOpts())
    assert len(exact) == OPENER_MAX_CHARS and exact.endswith("x" * room)
    assert over == greeting


def test_without_a_name_a_long_recap_still_welcomes_them_back() -> None:
    # A summary means they have talked before: leaving the recap out must not turn the
    # greeting into a first meeting that asks for their name.
    text = GREETER.build(name=None, summary=LONG_RECAP, now=MORNING, opts=GreetingOpts())
    assert text == "Welcome back."
