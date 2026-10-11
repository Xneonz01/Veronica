"""The claim check on a call (`tools/claims.py`): what her reply said she did and she
didn't, and the relay check (`tools/relayed.py`): what it passed on from a line written
to her in her memory or a file she read, shown apart from it, kept after it in history,
then said.

Kept as the user got it (bug 37): the next turn's "yes, do it" mustn't meet a history
that says it was done. It is kept once shown, as chat keeps what it shows under the
answer. Talked over, it stays with a reply heard whole and goes with one cut short
(`interrupt.py`), and one that can't be said still stands on screen and in history.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.services.tools import ToolOutcome, claims, relayed
from app.services.voice.realtime import spoken_confirm

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

logger = logging.getLogger(__name__)


async def settle(
    s: RealtimeSession,
    entry: dict[str, Any],
    ran: list[ToolOutcome],
    *,
    done: tuple[str, ...],
    memory: str | None = None,
) -> None:
    """Check the reply in `entry`, its place in history, against what its turn ran (and
    `done` outside a tool call) and what it read (its `memory` block and the tools'
    results), and give the caveat it needs. One that can't be shown or said is logged,
    and the reply stands."""
    apps = s.settings.capabilities.app_allowlist
    reply = entry["content"]
    found = claims.check(reply, ran, apps, surface="voice", done=done)
    read = [memory or "", *(outcome.text for outcome in ran)]
    kept = [*claims.caveats(found), *relayed.caveats(relayed.check(reply, read, surface="voice"))]
    caveat = " ".join(item["text"] for item in kept)
    if not caveat:
        return
    gen = s.audio_gen
    try:
        await s.emit_caveat(caveat)
        if s.audio_gen == gen:  # not talked over before it was shown
            s.reply_caveats = kept
            entry["content"] = claims.with_stored(reply, kept)
        await spoken_confirm.say_aside(s, caveat)
    except Exception:
        logger.exception("the caveat could not be said")
