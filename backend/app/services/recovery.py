"""Her recovery key, made and used (ADR 0001). Made on request and shown once, it opens
her data keys, her memory's and her voices', where Windows no longer can: another
Windows computer, Windows reinstalled, a reset account.

Making one seals every data key with it, each in its own key file, so a key file carries
its recovery sealing wherever it goes: all of them or none, under every key's lock at
once, taken in one order (`data_key.seal_recovery`). Only when every key opens and the
start found none of her data locked: a key sealed while her memory can't be opened with
it would be a recovery key that opens nothing. A new recovery key replaces the one
before, which stops opening them. Using one opens each key file it fits
(`data_key.recover`): one Windows can't unwrap here is wrapped for this account, and the
next start opens it. Each key file is read and written under its key's lock
(`core/file_lock.py`), which a start holds while it opens the key. Blocking: run it off
the event loop.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core import dpapi, recovery_key
from app.core.config import Settings
from app.core.data_key import (
    Locked,
    Recovery,
    RecoveryMark,
    key_path,
    open_key,
    recover,
    recovery_mark,
    seal_recovery,
)
from app.core.file_lock import held, lock_path
from app.paths import anchored
from app.services.voice.clips import KEY_NAME

logger = logging.getLogger(__name__)


class Unavailable(Exception):
    """A recovery key can't be made or used now. `reason`: `unsupported` (no DPAPI: her
    data isn't encrypted here), `locked` (a data key can't be opened, or opens nothing)
    or `nothing` (no data key yet)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NoneMade(Exception):
    """No key file holds a recovery key's sealing: none was made for this data."""


class Wrong(Exception):
    """The recovery key fits none of the key files: not theirs, or an older one."""


@dataclass(frozen=True, slots=True)
class Status:
    """When the recovery key that seals every data key was made (None when none does),
    whether only some are sealed with it (a key made since, or a make that failed to put
    a file back), and why one can't be made now (None when it can)."""

    made: str | None
    partial: bool
    unavailable: str | None


@dataclass(frozen=True, slots=True)
class Used:
    """The stores a recovery key opened for this account, those it fits that already open
    here, and those it doesn't fit."""

    restored: list[str] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


class RecoveryKeys:
    """Her data keys' files, by the store each opens, in the order their locks are taken:
    `memory`, then `voices`."""

    def __init__(self, keys: dict[str, Path]) -> None:
        self._keys = keys

    @classmethod
    def of(cls, settings: Settings) -> RecoveryKeys:
        return cls(
            {
                "memory": key_path(anchored(settings.memory.db_path)),
                "voices": anchored(settings.voice.voices_dir) / KEY_NAME,
            }
        )

    def status(self, *, locked: bool = False) -> Status:
        """What the key files say now; `locked` when the start found her data locked."""
        present = self._present()
        marks: dict[str, RecoveryMark | None] = {}
        every_key_opens = True
        for name, path in present.items():
            with held(lock_path(path)):
                marks[name] = recovery_mark(path)
                every_key_opens = every_key_opens and _opens(path)
        sealed_by = {mark.key_id for mark in marks.values() if mark is not None}
        whole = len(sealed_by) == 1 and None not in marks.values()
        made = next((mark.made for mark in marks.values() if mark is not None), None)
        return Status(
            made=made if whole else None,
            partial=bool(sealed_by) and not whole,
            unavailable=self._unavailable(present, locked or not every_key_opens),
        )

    def make(self, *, locked: bool = False) -> tuple[str, str]:
        """A new recovery key, as the person keeps it, and when it was made. Every data key
        is opened first, under all their locks at once, so none is sealed unless all
        can be; `locked` when the start found her data locked."""
        present = self._present()
        reason = self._unavailable(present, locked)
        if reason is not None:
            raise Unavailable(reason)
        with ExitStack() as locks:
            for path in present.values():
                locks.enter_context(held(lock_path(path)))
            keys = {path: _open(path) for path in present.values()}
            secret = recovery_key.new()
            made = datetime.now(UTC).isoformat(timespec="seconds")
            seal_recovery(keys, recovery_key.sealing_key(secret), recovery_key.key_id(secret), made)
        logger.info("made a recovery key for %s", ", ".join(present))
        return recovery_key.written(secret), made

    def use(self, text: str) -> Used:
        """Open every key file the recovery key in `text` fits. `NotARecoveryKey` for
        text that isn't one, `NoneMade` when no key file holds a sealing, `Wrong` when it
        fits none, `Unavailable` where there is no DPAPI to wrap a key for."""
        secret = recovery_key.read(text)
        if not dpapi.available():
            raise Unavailable("unsupported")
        sealing = recovery_key.sealing_key(secret)
        found: dict[str, Recovery] = {}
        for name, path in self._present().items():
            with held(lock_path(path)):
                found[name] = recover(path, sealing)
        if all(outcome is Recovery.NONE for outcome in found.values()):
            raise NoneMade
        used = Used(
            restored=[name for name, outcome in found.items() if outcome is Recovery.RESTORED],
            opened=[name for name, outcome in found.items() if outcome is Recovery.OPEN],
            unmatched=[
                name
                for name, outcome in found.items()
                if outcome in (Recovery.WRONG, Recovery.NONE)
            ],
        )
        if not used.restored and not used.opened:
            raise Wrong
        return used

    def _present(self) -> dict[str, Path]:
        return {name: path for name, path in self._keys.items() if path.is_file()}

    def _unavailable(self, present: dict[str, Path], locked: bool) -> str | None:
        if not dpapi.available():
            return "unsupported"
        if not present:
            return "nothing"
        return "locked" if locked else None


def _opens(path: Path) -> bool:
    try:
        open_key(path, encrypted=True)
    except Locked:
        return False
    return True


def _open(path: Path) -> bytes:
    """The data key from the key file at `path`; the caller holds its lock."""
    try:
        key = open_key(path, encrypted=True)
    except Locked as locked:
        raise Unavailable("locked") from locked
    if key is None:  # no DPAPI: checked before, so never
        raise Unavailable("unsupported")
    return key
