"""Form-of-address policy: a deterministic Tier-1 slot, not a fuzzy preference.

How Veronica addresses the user (by name or not) is enforced by code, not by
retrieval luck. It lives in the Tier-1 profile under one reserved key,
``address_by_name`` -> never | sparingly | often, beside the name itself — so a
correction ("stop using my name") reconciles with the name in a single slot rather
than stacking as a separate, gated preference that retrieval may or may not surface.

The Context Assembler reads this slot and emits an explicit directive into the
prompt (e.g. "Do not address the user by name.") instead of dumping the bare name
as an echo-able fact. The save tool, the in-call voice hook and the learning loop
route form-of-address requests here via ``classify_directive``, so a correction
always applies.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.services.memory.models import AddressByName

ADDRESS_KEY = "address_by_name"  # the reserved Tier-1 profile slot
NAME_KEYS = ("nickname", "name")  # checked in order; a nickname wins over the legal name


def coerce_address(value: object) -> AddressByName:
    """The stored slot value as a validated enum; anything unknown -> the default."""
    try:
        return AddressByName(str(value))
    except ValueError:
        return AddressByName.sparingly


def display_name(profile: Mapping[str, str]) -> str | None:
    """The name to address the user by (nickname preferred), or None if unknown."""
    for key in NAME_KEYS:
        value = profile.get(key)
        if value:
            return value
    return None


def address_directive(profile: Mapping[str, str]) -> str | None:
    """The prompt directive governing use of the user's name (None if nothing to say).

    Never dumps the raw name as a recitable fact; emits an instruction the model
    follows. ``never`` suppresses the name entirely (the model cannot echo what it
    is not given); ``sparingly``/``often`` state the name with a frequency cap.
    """
    setting = coerce_address(profile.get(ADDRESS_KEY))
    if setting is AddressByName.never:
        return "Do not address the user by name."
    name = display_name(profile)
    if not name:
        return None
    if setting is AddressByName.often:
        return (
            f"The user's name is {name}; address them by name when it feels natural, "
            "not in every reply."
        )
    return (
        f"The user's name is {name}. Use their name rarely — at most once near the "
        "start of a conversation, never in every reply."
    )


def address_summary(setting: AddressByName) -> str:
    """A calm, human one-liner for the notification when the slot changes."""
    return {
        AddressByName.never: "Won't use your name",
        AddressByName.sparingly: "Will use your name sparingly",
        AddressByName.often: "Will use your name more often",
    }[setting]


# -- natural-language intent -> the deterministic slot --------------------------

# Negation + a form-of-address verb + an address-target "name" (my/by/their/your
# name): "stop using my name", "don't call me by name", "doesn't want to be
# addressed by name". The verb and "name" stay within one clause (no .?! between).
_NEVER = re.compile(
    r"(?i)\b(?:don['’]?t|do\s+not|dont|doesn['’]?t|does\s+not|won['’]?t|wont|"
    r"not\s+to|stop|quit|no\s+need\s+to|dis?likes?)\b"
    r"[^.?!]{0,40}?\b(?:call(?:ed|ing)?|address(?:ed|ing)?|us(?:e|ed|ing)|say(?:ing)?|said)\b"
    r"[^.?!]{0,20}?\b(?:my|by|their|your)\s+name\b"
)
# Positive request to be named more — kept tight so a declarative "I use my name
# online" does not trigger: requires "more", a "please", or a "call/address me by".
_OFTEN = re.compile(
    r"(?i)(?:"
    r"\b(?:use|using|say|saying)\s+my\s+name\s+more\b"
    r"|\bplease\s+(?:use|say)\s+my\s+name\b"
    r"|\bcall\s+me\s+by\s+(?:my\s+)?name\b"
    r"|\baddress\s+me\s+by\s+(?:my\s+)?name\b"
    r"|\b(?:likes?|prefers?|wants?)\s+(?:to\s+be\s+)?(?:called|addressed)\s+by\s+(?:their\s+)?name\b"
    r")"
)


def classify_directive(text: str) -> AddressByName | None:
    """Map a form-of-address request to the slot value, or None if it isn't one.

    Negation wins, so "don't use my name" is ``never`` even though it also contains
    "use ... name". Deterministic and conservative — unrelated text returns None so
    an ordinary preference is never hijacked.
    """
    if _NEVER.search(text):
        return AddressByName.never
    if _OFTEN.search(text):
        return AddressByName.often
    return None
