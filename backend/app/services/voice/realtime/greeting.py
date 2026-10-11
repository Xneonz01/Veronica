"""The greeting spoken when a call starts: deterministic, varied, no LLM latency.

Composed from the Tier-1 profile (nickname or name), the time of day, and now and
then a callback to the last session summary. Selection is seeded by the current
time, so it varies from call to call yet is reproducible in tests. The call-start
path depends only on the ``Greeter`` protocol, not on how the text is produced.

The opener is held to about six seconds of speech (`OPENER_MAX_CHARS`). The summary is
one sentence the model wrote at the end of the last call, with no length of its own,
so it is what gets left out when it doesn't fit: whole, never cut mid-sentence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.services.memory.models import AddressByName

# How fast the synthesiser speaks, fitted as `seconds = FIXED_S + PER_CHAR_S x characters`
# by `scripts/speech_rate.py`: Chatterbox Turbo in the default voice, twenty readings of
# ten openers, 2026-09-24. That is 17.8 characters a second once she is speaking. A
# cloned voice keeps its own pace; this is the voice the app ships with. Re-measure when
# the synthesiser changes, and these move together.
FIXED_S = 0.58
PER_CHAR_S = 0.0563
# Past about six seconds the opener is a monologue the user has to wait out, or talk
# over, before they can say why they called.
OPENER_SECONDS = 6.0
OPENER_MAX_CHARS = int((OPENER_SECONDS - FIXED_S) / PER_CHAR_S)  # 96


@dataclass(frozen=True)
class GreetingOpts:
    enabled: bool = True
    length: str = "warm"  # "brief" | "warm"
    callback: bool = True  # allow the occasional session-summary callback


def parse_opts(raw: object) -> GreetingOpts:
    """Read greeting options from the `call_start` control message (set by the frontend)."""
    if not isinstance(raw, dict):
        return GreetingOpts()
    return GreetingOpts(
        enabled=bool(raw.get("enabled", True)),
        length=str(raw.get("length", "warm")),
        callback=bool(raw.get("callback", True)),
    )


class Greeter(Protocol):
    def build(
        self,
        *,
        name: str | None,
        summary: str | None,
        now: datetime,
        opts: GreetingOpts,
        address: AddressByName = AddressByName.sparingly,
    ) -> str: ...


_FIRST_MEETING = [
    "Hi — I'm Veronica. I don't think we've met. What should I call you?",
    "Hello. I'm Veronica. What's your name?",
]
_TIME_HELLO = {
    "morning": ["Morning", "Good morning"],
    "afternoon": ["Afternoon", "Good afternoon"],
    "evening": ["Evening", "Good evening"],
    "late": ["Hey", "Still up"],
}
_RETURNING = [
    "{hello}, {name}.",
    "{hello}, {name} — good to hear you.",
    "{hello}, {name}. Back with me?",
    "Welcome back, {name}.",
]
# Returning, but the user asked us never to use their name: warm, name-less, no ask.
_RETURNING_NONAME = [
    "{hello}.",
    "{hello} — good to hear you.",
    "Welcome back.",
]


def _part_of_day(now: datetime) -> str:
    hour = now.hour
    if hour < 5 or hour >= 22:
        return "late"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _seed(now: datetime) -> int:
    return now.hour * 3600 + now.minute * 60 + now.second


def _pick(pool: list[str], now: datetime, offset: int = 0) -> str:
    return pool[(_seed(now) + offset) % len(pool)]


def _occasional(now: datetime) -> bool:
    return _seed(now) % 3 == 0  # ~1 in 3 calls gets the "last time…" callback


def _with_recap(greeting: str, summary: str | None) -> str:
    """The greeting followed by the last call's recap, or the greeting alone when the
    two would run past `OPENER_MAX_CHARS`."""
    opener = f"{greeting} Last time, {summary}"
    return opener if summary and len(opener) <= OPENER_MAX_CHARS else greeting


class TemplateGreeter:
    """Deterministic template-pool greeter; the default, with no LLM involved."""

    def build(
        self,
        *,
        name: str | None,
        summary: str | None,
        now: datetime,
        opts: GreetingOpts,
        address: AddressByName = AddressByName.sparingly,
    ) -> str:
        if not opts.enabled:
            return ""
        wants_callback = opts.callback and opts.length != "brief" and bool(summary)
        use_name = None if address is AddressByName.never else name
        if use_name:
            hello = _pick(_TIME_HELLO[_part_of_day(now)], now)
            greeting = _pick(_RETURNING, now, offset=1).format(hello=hello, name=use_name)
            if wants_callback and _occasional(now):
                return _with_recap(greeting, summary)
            return greeting
        # No usable name. With a summary, it is a returning user: welcome them back, with
        # the recap if it fits. If a name is on file but suppressed ("never"), greet
        # warmly without it and don't ask for it.
        if wants_callback:
            return _with_recap("Welcome back.", summary)
        if name:
            hello = _pick(_TIME_HELLO[_part_of_day(now)], now)
            return _pick(_RETURNING_NONAME, now, offset=1).format(hello=hello)
        return _pick(_FIRST_MEETING, now)
