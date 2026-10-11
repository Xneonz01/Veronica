"""What keeps Veronica from working fully after a start, as a code the app has words for.

Opening memory.db can fail in ways the person can fix: a newer Veronica wrote it, it is
damaged, something holds it or its folder is closed to her, there isn't room on the
disk, or the copy kept before an upgrade couldn't be made. Those used to stop the
backend, and the app then only said it couldn't reach it; now the start goes on without
what lives in that file (`app/stores.py`). So does one that is still where an earlier
version kept it while the settings keep it elsewhere (`app/left_behind.py`), rather
than start a second, empty memory. A model that isn't on disk used to be said
only in the log (`app/startup.py`), and a voices file she couldn't read stopped the
backend. `/health` reports each as a problem, and the app
says what happened and what to do (`frontend/src/shell/ProblemBanner.tsx`).

One is found after the start, and can be put right without a restart: an activity log
changed outside Veronica (`audit_broken`), found by the walk that runs in the
background, and cleared when the person marks it as reviewed. And a lock a recovery key
opens gives way to `data_recovered`, which says to restart. Each is announced
(`announce`), so every surface reads the problems again.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.core.event_bus import EventBus
from app.schemas.events import Event

ProblemCode = Literal[
    "memory_too_new",
    "memory_damaged",
    "memory_unopenable",
    "memory_no_room",
    "memory_copy_failed",
    "memory_left_behind",
    "memory_locked",
    "memory_not_encrypted",
    "models_missing",
    "voices_damaged",
    "voices_unusable",
    "voices_locked",
    "data_recovered",
    "audit_broken",
]
#: Published when the problems change after the start, which the event lane tells
#: every surface as `problems_changed`: each reads `/health` again.
PROBLEMS_CHANGED = "problems.changed"


class Problem(BaseModel):
    """One thing wrong: a code, and only the values its words need. Never an exception's
    text or a path, which the app would have to show as it came. `memory_no_room` has
    `needed_mb` and `free_mb` when the copy before an upgrade found too little room, and
    none when the disk filled mid-write; `memory_too_new` has the newest copy the running
    version can open, or an empty `copy`; `memory_locked` and `voices_locked` have the
    `reason` (`data_key.Locked`), and `recoverable` (1) when a recovery key can open
    them; `data_recovered` has `memory` and `voices` (1) for those a recovery key
    opened for this account, which a restart opens; `memory_not_encrypted` has `copy`,
    the name of a copy kept from before an upgrade, when it was one that couldn't be
    encrypted, `needed_mb` and `free_mb` when there wasn't room, and `reason`
    `unsupported` where the system has no DPAPI; `models_missing` has `models`, the codes
    of the models not on disk (`adapters/model_files.py`), joined by commas;
    `audit_broken` has `row`, the first entry that doesn't add up (0 when only the record
    of where the log ends is wrong)."""

    code: ProblemCode
    detail: dict[str, str | int] = {}


async def announce(bus: EventBus) -> None:
    """Tell every surface the problems changed after the start (`PROBLEMS_CHANGED`)."""
    await bus.publish(Event(type=PROBLEMS_CHANGED, source="app", payload={}))
