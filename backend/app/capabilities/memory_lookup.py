"""Which memories a phrase points at, and what an explicit recall lists when the
relevance gate finds nothing. Used by the memory capabilities (`memory.py`)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.capabilities.memory_args import CorrectArgs, ForgetArgs
from app.capabilities.memory_profile import profile_slots
from app.kernel.capability import CapabilityResult
from app.kernel.errors import Code
from app.services.memory.assembler import render_block
from app.services.memory.models import Memory, MemoryKind, RetrievedContext
from app.services.memory.normalize import content_tokens, normalize

if TYPE_CHECKING:
    from app.services.memory.runtime import MemoryRuntime

# A correction or a forget touches at most this many memories. A phrase that matches
# half of what she knows is a mistake, not an instruction: refusing can be retried, and
# forty forgotten memories can't be brought back from a notification.
MAX_MATCHES = 5
# The share of a phrase's words a memory must contain to be what it points at. Asked
# about, a forget or a correction names the memory it matched, and the user sees which.
# A correction that runs unasked takes a memory only when it holds every word: "User
# takes tea with milk" shared two words of three with the coffee one, and replaced it.
LOOSE = 0.6
EVERY_WORD = 1.0
_INVENTORY_CAP = 10  # facts listed when an explicit recall matches nothing specific
INVENTORY_HEADING = "What you currently know about the user:"
# What a person thinks of as what she remembers about them. The profile has slots of
# its own, and a session summary is her recap, not something she was told.
_TOLD = (MemoryKind.fact, MemoryKind.preference, MemoryKind.episodic)
# Every active memory of a kind: the listing stops at the newest 200 by default, and
# pinned memories or a raised cap keep more than that. SQLite reads a negative LIMIT as
# no limit.
_ALL = -1
# How her memory block labels a line ("Recall — "), which a quoting model may copy.
_LABEL = re.compile(r"^\w+ — ")
# Every memory she saves starts with "User", and a possessive leaves an "s" behind:
# neither says which memory a phrase means.
_NOT_WHICH = frozenset({"user", "s"})
_WORD = re.compile(r"\w+")


def _bare(text: str) -> str:
    """`text` normalised, without a block label before it or a full stop after it."""
    return _LABEL.sub("", normalize(text)).rstrip(".!?…").strip()


def _words(text: str) -> set[str]:
    return content_tokens(text) - _NOT_WHICH


def matches(items: list[Memory], phrase: str, *, overlap: float = LOOSE) -> list[Memory]:
    """The memories a phrase points at.

    A phrase with no words of its own ("User", "User's") points at nothing: as a whole
    phrase it is in every memory. Quoted from a memory, as the arguments ask, it points
    at the memories that hold it as whole words, and only those: sharing most of its
    words, "User takes their tea with milk" matched a forget of the coffee one.
    Otherwise, at the memories holding at least `overlap` of its words, most first. Not
    by embedding: the model has already narrowed what the user meant to a phrase, and
    this runs while they wait.
    """
    wanted = _words(phrase)
    if not wanted:
        return []
    whole = _bare(phrase)
    within = re.compile(rf"(?<!\w){re.escape(whole)}(?!\w)")
    exact = [item for item in items if within.search(_bare(item.content))]
    if exact:
        return exact
    scored: list[tuple[float, Memory]] = []
    for item in items:
        share = len(wanted & _words(item.content)) / len(wanted)
        if share >= overlap:
            scored.append((share, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


def the_one(found: list[Memory], phrase: str) -> Memory | None:
    """The memory a correction means: the only match, or the one the phrase equals."""
    if len(found) == 1:
        return found[0]
    whole = _bare(phrase)
    return next((item for item in found if _bare(item.content) == whole), None)


def nothing_matches() -> CapabilityResult:
    # Said so that the model tries again in the memory's own words. "I don't have
    # anything like that stored" was retold as "I already cleared that".
    return CapabilityResult(
        ok=False, code=Code.not_found.value, detail="Nothing I remember matches those words."
    )


def unmatched(found: list[Memory]) -> CapabilityResult | None:
    """The refusal a correction or a forget gets for what it matched, if any."""
    if not found:
        return nothing_matches()
    if len(found) > MAX_MATCHES:
        return CapabilityResult(
            ok=False,
            code=Code.too_many_matches.value,
            detail="That matches too much of what I know.",
        )
    return None


def ambiguous() -> CapabilityResult:
    """A correction's refusal when its words fit more than one memory: which one is wrong
    is the user's to say, not the newest one's to lose."""
    return CapabilityResult(
        ok=False,
        code=Code.too_many_matches.value,
        detail="That matches more than one thing I remember. Quote the one you mean.",
    )


def quoted_text(text: str) -> str:
    """`text` as a prompt shows a memory: in quotes, without its full stop."""
    return f"“{text.strip().rstrip('.')}”"


def quoted(found: list[Memory]) -> str:
    """Memories as a prompt names them: “A”, “B” and “C”."""
    names = [quoted_text(item.content) for item in found]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


async def told(memory: MemoryRuntime) -> list[Memory]:
    """Everything a correction or a forget may point at."""
    found: list[Memory] = []
    for kind in _TOLD:
        found += await memory.service.list(kind=kind, pending=False, limit=_ALL)
    return found


async def still_active(memory: MemoryRuntime, ids: tuple[int, ...]) -> list[Memory]:
    """The memories a prompt named that are still there to change, in its order."""
    found = [await memory.service.get(memory_id) for memory_id in ids]
    return [item for item in found if item is not None and not item.archived]


async def inventory(memory: MemoryRuntime, retrieved: RetrievedContext) -> str | None:
    """What she knows that is stable, for an explicit recall the relevance gate found
    nothing for: her profile, then her newest facts and preferences. The user asked what
    she knows, so this is a browse; it doesn't relax the gate the per-turn memory block
    goes through. The profile alone used to stand in for the list whenever a name was
    set, and the list never ran."""
    prefs = await memory.service.list(kind=MemoryKind.preference, pending=False)
    facts = await memory.service.list(kind=MemoryKind.fact, pending=False)
    items = sorted(prefs + facts, key=lambda m: m.updated_at, reverse=True)[:_INVENTORY_CAP]
    lines = [f"- {item.content}" for item in items]
    head = render_block(retrieved, preamble=INVENTORY_HEADING)
    if head is None:
        return f"{INVENTORY_HEADING}\n" + "\n".join(lines) if lines else None
    return "\n".join([head, *lines])


async def meant(
    memory: MemoryRuntime, args: CorrectArgs, *, overlap: float = EVERY_WORD
) -> Memory | CapabilityResult:
    """The memory a correction replaces, or why it can't be found."""
    if args.named is not None:  # what the prompt named, while it is still there
        named = await still_active(memory, args.named)
        return named[0] if named else nothing_matches()
    found = matches(await told(memory), args.old, overlap=overlap)
    if not found:  # her profile, then: a slot of its own (bug 38)
        slots = await profile_slots(memory, args.old)
        return (slots[0] if len(slots) == 1 else ambiguous()) if slots else nothing_matches()
    return unmatched(found) or the_one(found, args.old) or ambiguous()


async def targets(memory: MemoryRuntime, args: ForgetArgs) -> list[Memory] | CapabilityResult:
    """The memories a forget removes, or why it can't find any."""
    if args.named is not None:  # exactly what the prompt named, while it is there
        found = await still_active(memory, args.named)
    else:
        found = matches(await told(memory), args.query)
        if not found:  # her profile, then: slots of their own (bug 38)
            found = await profile_slots(memory, args.query)
    return unmatched(found) or found
