"""Her voice clips, sealed at rest (ADR 0001). Each reference clip is a recording of a
real person, so it is kept sealed with the voices' own key (`voices.key`, beside the
clips, `core/data_key.py`) and opened into memory when read (`core/sealed.py`), never to
disk.

At the start, the plain clips in the folder are sealed in place, each checked to open
back to the very bytes it held before it takes the plain one's place, orphans (clips a
set-aside voices.json no longer names) included. A key the clips can't be opened with
(another Windows account, a damaged key file, sealed clips with none beside them)
leaves her voices locked: listed, never opened or written, said (`voices_locked`), and
nothing is written over. So does a folder or key file she can't read or write
(`UNUSABLE`, said as `voices_unusable`). A clip left plain (a start that couldn't seal
it) still opens. The key's lock (`core/file_lock.py`) is held across opening the key
and sealing, as her memory's is. Where the system has no DPAPI to keep a key with (not
Windows), there is none: the clips stay plain and new ones are written plain.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO

from app.core.data_key import Locked, open_key
from app.core.file_lock import held, lock_path
from app.core.sealed import MAGIC, is_sealed, seal, seal_in_place, unseal

logger = logging.getLogger(__name__)

KEY_NAME = "voices.key"
# Why the clips are locked when it isn't the key's fault: the folder, or the key file in
# it, couldn't be read or written.
UNUSABLE = "unusable"
_SEALING = ".sealing"


class Clips:
    """The sealed clips of one voices folder: opened into memory, written sealed."""

    def __init__(
        self, key: bytes | None, locked: str | None = None, *, recoverable: bool = False
    ) -> None:
        self._key = key  # None and not locked: the system has no key to keep; plain
        self.locked = locked  # why the key couldn't be used; None when it could
        self.recoverable = recoverable  # a recovery key can open it (`data_key.recover`)

    @classmethod
    def open(cls, folder: Path) -> Clips:
        """The clips in `folder`: their key opened, made when nothing is sealed yet, and
        the plain ones sealed. Blocking: it reads and writes the folder."""
        try:
            with held(lock_path(folder / KEY_NAME)):
                return cls._open(folder)
        except Locked as locked:
            logger.error("her voices are locked: %s", locked.reason)
            return cls(None, locked.reason, recoverable=locked.recoverable)
        except OSError as error:  # the folder closed to her, a file where it should be
            logger.error("her voices' key can't be read or made (%s)", type(error).__name__)
            return cls(None, UNUSABLE)

    @classmethod
    def _open(cls, folder: Path) -> Clips:
        clips = sorted(folder.glob("*.wav"))
        key = open_key(folder / KEY_NAME, encrypted=any(map(_sealed, clips)))
        if key is None:
            return cls(None)
        sealed = 0
        for clip in clips:
            try:
                clip.with_name(clip.name + _SEALING).unlink(missing_ok=True)
                if not is_sealed(clip):
                    seal_in_place(clip, key, marker=_SEALING)
                    sealed += 1
            except OSError as error:  # left as it is, still opened; tried again next time
                logger.warning("a voice clip stays plain for now (%s)", type(error).__name__)
        if sealed:
            logger.info("sealed %d voice clips", sealed)
        return cls(key)

    def read(self, path: str | Path) -> bytes:
        """The clip at `path`, in memory. `Locked` for a sealed one while the key can't be
        used; `SealBroken` for one changed or put in another's place."""
        file = Path(path)
        data = file.read_bytes()
        if not data.startswith(MAGIC):
            return data  # still plain
        if self._key is None:
            raise Locked(self.locked or "key_missing")
        return unseal(data, self._key, file.name)

    def write(self, handle: BinaryIO, path: Path, wav: bytes) -> None:
        """Write the clip `wav`, sealed, into `handle`, the file at `path` made for it;
        plain where the system has no key to keep."""
        if self.locked is not None:
            raise Locked(self.locked)
        handle.write(wav if self._key is None else seal(wav, self._key, path.name))


def _sealed(clip: Path) -> bool:
    """Whether `clip` is sealed. One that can't be read is taken as sealed, so that no new
    key is made that could lock it away."""
    try:
        return is_sealed(clip)
    except FileNotFoundError:
        return False
    except OSError:
        return True
