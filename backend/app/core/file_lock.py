"""An exclusive lock, across processes, on a small file beside what it guards.

Two starts at once (a second backend started by mistake runs its start before it finds
the port taken) must not each open or make a data key and encrypt with it: one would
encrypt with a key the other then set aside as unusable (`data_key.py`). The lock is
the system's own on the file's first byte (`msvcrt.locking` on Windows, `fcntl.flock`
elsewhere), so it is let go when its holder closes the file or ends, crash included.
The file stays, empty.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Long enough for the other start to encrypt a large memory file under it: a 70 MB one
# took 0.87 s (ARCHITECTURE §15.1).
WAIT_S = 60.0
_POLL_S = 0.05


class Held(OSError):
    """Another start kept the lock for longer than a start waits."""


def lock_path(key: Path) -> Path:
    """The lock guarding the key file at `key` and what is encrypted with it:
    `memory.key` -> `memory.lock`."""
    return key.with_suffix(".lock")


@contextmanager
def held(path: Path, *, wait_s: float = WAIT_S) -> Iterator[None]:
    """The lock on `path`, the file made if missing, for the block. Blocking: waits up to
    `wait_s` for another holder, then `Held`; `OSError` if the file can't be made."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait_s
        while not _take(fd):
            if time.monotonic() >= deadline:
                raise Held(f"{path.name} is held by another start")
            time.sleep(_POLL_S)
        try:
            yield
        finally:
            _release(fd)
    finally:
        os.close(fd)


if sys.platform == "win32":
    import errno
    import msvcrt

    def _take(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except PermissionError as error:  # EACCES: another handle holds it
            if error.errno != errno.EACCES:
                raise
            return False
        return True

    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _take(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
