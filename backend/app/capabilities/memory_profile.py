"""A correction or a forget that means her profile ("About you": a name, where they live),
which is slots of its own rather than facts (bug 38: "that's part of your profile, which
I can't change from here", and the Memory page was the only way).

A slot is found by its value: the phrase the model wrote to point at it holds the value
the prompt showed ("The user's name is Rowan"). What a correction sets it to is read from
the new phrase, and only when that is certain: in the profile's own form ("name:
Morgan"), or as what took the old value's place in the same sentence ("The user's name
is Morgan"). A name is not to be guessed, so anything else is refused with the form to
use, and the model says it again that way.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.kernel.capability import CapabilityResult
from app.kernel.errors import Code
from app.services.memory.address import ADDRESS_KEY
from app.services.memory.models import Memory, MemoryKind
from app.services.memory.normalize import normalize

if TYPE_CHECKING:
    from app.services.memory.runtime import MemoryRuntime

_WORD = re.compile(r"\w+")
# "name: Morgan", as her prompt shows the profile ("Profile — location: Leeds").
_KEYED = re.compile(r"^\s*([\w ]+?)\s*:\s*(.+?)\s*$")
# Around a value: what a sentence ends with, and what quotes it.
_TRIM = " \t.,;:!?\"'“”‘’"
_ARTICLE = re.compile(r"^\s*(?:the|a|an)\s+", re.IGNORECASE)


def _words(text: str) -> str:
    return " ".join(_WORD.findall(normalize(text)))


async def profile_slots(memory: MemoryRuntime, phrase: str) -> list[Memory]:
    """The profile slots `phrase` points at: those whose value it holds as whole words;
    of several, those whose name it says too ("name" in "The user's name is Rowan"). The
    form-of-address slot is the save's to change, never these."""
    said = f" {_words(phrase)} "
    slots = await memory.service.list(kind=MemoryKind.profile, pending=False, limit=-1)
    held = [
        slot
        for slot in slots
        if slot.key
        and slot.key != ADDRESS_KEY
        and _words(slot.content)
        and f" {_words(slot.content)} " in said
    ]
    named = [slot for slot in held if slot.key and f" {_words(slot.key)} " in said]
    return named if len(held) > 1 and named else held


def slot_value(slot: Memory, old: str, new: str) -> str | None:
    """What a correction sets `slot` to, or None when `new` doesn't say it for certain."""
    keyed = _KEYED.match(new)
    if keyed and _words(keyed.group(1)) == _words(slot.key or ""):
        value = keyed.group(2).strip(_TRIM)
        return value or None
    found = re.search(rf"(?<!\w){re.escape(slot.content)}(?!\w)", old, re.IGNORECASE)
    if found is None:
        return None
    # Quoted from her prompt ("The user's name is Rowan") and written as she saves one
    # ("User's name is Morgan."): an article at the start is no part of the frame.
    before = _ARTICLE.sub("", old[: found.start()])
    after = old[found.end() :].rstrip(_TRIM)
    changed = _ARTICLE.sub("", new).rstrip(_TRIM)
    fits = changed.lower().startswith(before.lower()) and changed.lower().endswith(after.lower())
    if not fits or len(changed) <= len(before) + len(after):
        return None
    value = changed[len(before) : len(changed) - len(after)].strip(_TRIM)
    return value if value and value.casefold() != slot.content.casefold() else None


def unreadable(slot: Memory) -> CapabilityResult:
    """The refusal for a correction of `slot` whose new value can't be told for certain:
    it says the form to use, so the model's next try is one that can."""
    return CapabilityResult(
        ok=False,
        code=Code.invalid_args.value,
        detail=(
            f"That's your {slot.key} in your profile, “{slot.content}”. Say what it is now "
            f"as “{slot.key}: …”, with the new {slot.key} in place of the dots."
        ),
    )


def described(items: list[Memory]) -> str:
    """Memories as a prompt names them, a profile slot by what it is: your name (“Rowan”)."""
    names = [
        f"your {item.key} (“{item.content}”)"
        if item.kind is MemoryKind.profile and item.key
        else f"“{item.content.strip().rstrip('.')}”"
        for item in items
    ]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
