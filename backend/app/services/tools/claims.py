"""The claim check: a reply that says she did something, read against what ran.

Asked to change her memory, the model sometimes answered as if it had and called no
tool: "I'll update that memory", "I've forgotten that you go to the gym". The words are
the only trace, so they are read the way the product reads anything it can't trust,
with a closed table (`claims_table.py`), and a claim that nothing of its kind backed
gets a caveat.

**Backed** means a call that can do what the claim says ran and succeeded this turn:
saving is backed by a save or a correction, changing and forgetting by a correction or
a forget, a file by a write, an app by an open. A promise ("I'll write that") is kept
by an attempt too: the call said how it went. A claim that a declined or timed-out call
would have backed gets none: the user just said no, and knows.

**The caveat is always true.** It names what didn't happen. When some other action of
the family did run, it says which part didn't: a save reported as an update ("I've
updated your preferences") gets "I saved that as a new memory; nothing older was
changed", never "My memory didn't change".

**The reply is never changed.** Chat keeps the caveat beside the message and shows it
under the answer; a call says it after the reply and shows it apart from her words. The
model reads it back where the user got it, after her reply in the history it is given
next turn (`with_stored`, since 0.34.12, bug 37): without it, a "yes, do it" met a
history that said it was done. Measured on qwen3.5:9b, asked again after an unbacked
claim, the tool ran 30 times in 30 in chat (18 without it) and 28 in 30 on a call (8);
asked "Did that work?", she said yes once in 15 on each (6 and 13 without); and in 135
replies to anything else the caveat's words came back once.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Iterable, Mapping
from typing import Literal

from app.services.tools import relayed
from app.services.tools.broker import ToolOutcome
from app.services.tools.claims_table import Kind, claimed
from app.telemetry import current_blackbox, current_perf

logger = logging.getLogger(__name__)

Family = Literal["memory", "files", "apps"]
# The caveat a reply gets: a family's; "saved" when the words fit more than one ("I've
# saved it" beside nothing that says what it was) or a save is all that didn't happen;
# "added" and "forgotten" when another memory action ran and this one didn't.
Caveat = Literal["memory", "files", "apps", "saved", "added", "forgotten"]

# Every capability that acts rather than reads, by family. A test walks the registry
# and fails on one missing here, so a new action can't ship with its claims unread.
FAMILY_OF: dict[str, Family] = {
    "memory.save": "memory",
    "memory.correct": "memory",
    "memory.forget": "memory",
    "files.write": "files",
    "apps.open": "apps",
}

BACKED_BY: dict[Kind, frozenset[str]] = {
    "memory_saved": frozenset({"memory.save", "memory.correct"}),
    "memory_changed": frozenset({"memory.correct", "memory.forget"}),
    "memory_forgotten": frozenset({"memory.forget", "memory.correct"}),
    "files": frozenset({"files.write"}),
    "apps": frozenset({"apps.open"}),
}
_FAMILY_OF_KIND: dict[Kind, Family] = {
    "memory_saved": "memory",
    "memory_changed": "memory",
    "memory_forgotten": "memory",
    "files": "files",
    "apps": "apps",
}
_TURNED_DOWN = frozenset({"confirm_denied", "confirm_timeout"})
# What a memory claim gets when another memory action did run: only its own part failed.
_MEMORY_PART: dict[Kind, Caveat] = {
    "memory_saved": "saved",
    "memory_changed": "added",
    "memory_forgotten": "forgotten",
}

# In her voice, shown under the answer or said after it.
CAVEAT: dict[Caveat, str] = {
    "memory": "My memory didn't actually change just now. Ask me again if you want it to.",
    "files": "No file was actually written just now. Ask me again if you want it written.",
    "apps": "No app was actually opened just now. Ask me again if you want it opened.",
    "saved": "Nothing was actually saved just now. Ask me again if you want it saved.",
    "added": "I saved that as a new memory; nothing older was changed.",
    "forgotten": "Nothing was actually forgotten just now. Ask me again if you want it forgotten.",
}
# The caveats that go back into the model's history after her reply.
_KEPT = frozenset({*CAVEAT, relayed.FAMILY})


def unsupported(
    text: str,
    ran: Iterable[str],
    app_names: Collection[str] = (),
    *,
    declined: Iterable[str] = (),
    attempted: Iterable[str] = (),
) -> list[Caveat]:
    """The caveats `text` needs, each once, in reading order.

    `ran` are the capabilities that ran and succeeded this turn (dotted names);
    `declined` the ones the user turned down or let time out; `attempted` every one
    that was called, whatever came of it.
    """
    done = set(ran)
    refused = set(declined)
    tried = set(attempted) | done
    memory_ran = any(FAMILY_OF.get(name) == "memory" for name in done)
    missing: list[Caveat] = []
    for claim in claimed(text, app_names):
        backing = frozenset().union(*(BACKED_BY[kind] for kind in claim.kinds))
        if done & backing or refused & backing or (claim.promise and tried & backing):
            continue
        caveat = _caveat(claim.kinds, memory_ran)
        if caveat not in missing:
            missing.append(caveat)
    return missing


def _caveat(kinds: tuple[Kind, ...], memory_ran: bool) -> Caveat:
    families = {_FAMILY_OF_KIND[kind] for kind in kinds}
    if len(families) > 1:
        return "saved"
    family = families.pop()
    if family == "memory" and memory_ran:
        return _MEMORY_PART[kinds[0]]
    return family


def check(
    reply: str,
    outcomes: Iterable[ToolOutcome],
    app_names: Collection[str],
    *,
    surface: str,
    done: Iterable[str] = (),
) -> list[Caveat]:
    """The caveats `reply` needs against what its turn's calls produced.

    `done` names capabilities whose work happened outside a tool call this turn (the
    call's spoken "stop using my name", written before the reply). Never raises: it
    runs on an answer already given, and a failure here must not turn a good turn into
    an error. The black box keeps the caveats and the surface, never the reply, which is
    the user's conversation.
    """
    started = time.perf_counter()
    try:
        calls = list(outcomes)
        ran = [call.capability for call in calls if call.ok and call.capability]
        declined = [
            call.capability for call in calls if call.code in _TURNED_DOWN and call.capability
        ]
        attempted = [call.capability for call in calls if call.capability]
        caveats = unsupported(
            reply, [*ran, *done], app_names, declined=declined, attempted=attempted
        )
    except Exception:
        logger.exception("the claim check failed; the reply stands unchecked")
        return []
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    current_perf().log("claims.check", elapsed_ms, caught=len(caveats))
    if caveats:
        current_blackbox().record("warn", "claims.unsupported", families=caveats, surface=surface)
    return caveats


def caveats(found: Iterable[Caveat]) -> list[dict[str, str]]:
    """The caveats as chat keeps and sends them: each once, with its sentence."""
    return [{"family": caveat, "text": CAVEAT[caveat]} for caveat in found]


def with_caveats(reply: str, found: Iterable[Caveat]) -> str:
    """The reply as the user got it, each caveat's sentence after it: what the model
    reads back next turn."""
    return _after(reply, [CAVEAT[caveat] for caveat in found])


def with_stored(reply: str, stored: Iterable[Mapping[str, str]]) -> str:
    """The same from caveats as they are kept: among those given, the claims' and the
    relay check's (`relayed.py`), in the words the user saw then. A search's note
    (`missed_...`) isn't one."""
    kept = [item.get("text", "") for item in stored if item.get("family") in _KEPT]
    return _after(reply, kept)


def _after(reply: str, sentences: Iterable[str]) -> str:
    said = " ".join(sentence for sentence in sentences if sentence)
    return f"{reply}\n\n{said}" if said else reply
