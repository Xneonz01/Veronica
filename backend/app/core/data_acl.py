"""Keep her data folder private to the account she runs as.

By default `data/` inherits its permissions from where Veronica was installed, and on
Windows that usually lets every signed-in account on the machine (Authenticated Users)
read and change it: her memories, conversations, voice clips and audit log. This makes
the folder private: inheritance off, full control for the running account, SYSTEM and
Administrators, and no entry for anyone else. SYSTEM and Administrators stay, as on a
Windows profile folder: an administrator can take ownership of anything anyway, and a
backup or antivirus service running as SYSTEM would otherwise lose the folder.

**Read and written as SDDL, through the Win32 security API** (`win_security.py`), never
through icacls. icacls prints account names in the system's language and code page, so
matching its text was blind on a German Windows and crashed on an account named with an
accented letter; and it looks up every account it is given, so an entry for an account
that no longer exists (deleted, or the folder restored from another machine) made the
whole repair fail. SDDL names every entry by SID (or a fixed alias: SY, BA, WD), the
same everywhere, and needs no lookup. An entry counts as private when it is owned by
the running account, SYSTEM or Administrators and grants nothing to anyone else (an
owner can always change the permissions again); at the top, inheritance must also be
off, so a change above can't reach inside, and the running account granted.

**Repaired, not stripped.** The top gets exactly those three entries, whatever it had.
Below it, an entry that still grants someone else loses its own entries and inherits
from its folder again, so it ends with what the top grants: removing names from a
folder that inherited nothing left it with no entry at all, and its owner locked out.
One another account owns is taken back. The walk never goes through a junction or a
link, which point outside the folder at things that are not hers, nor into a file with
a second name elsewhere, whose permissions the other name shares. A repair goes on past
what it can't change and fails at the end, so the next start tries again rather than
finding the top private and calling the rest fine.

**Checked at every start,** first, before the black box writes its own files into the
folder, and all of it: a file moved in or restored keeps permissions of its own, at any
depth (7 ms for a hundred and fifty entries, measured). A folder on a network share
or a drive without permissions (FAT) is left as it is and recorded as unsupported; one
that would take more than her data with it (a drive, the installation, the home folder,
a folder Windows or other programs keep their things in) is refused. Elsewhere than
Windows the folder is set to 0o700.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.win_security import (
    DataAclError,
    current_user_sid,
    full_sid,
    keeps_permissions,
    native,
    security,
    set_dacl,
    set_owner,
)
from app.paths import INSTALL_ROOT
from app.telemetry import current_blackbox
from app.telemetry.blackbox import Level

logger = logging.getLogger(__name__)

Outcome = Literal["ok", "created", "repaired", "unsupported", "refused"]
_Kind = Literal["folder", "file", "elsewhere", "unreadable"]

_KEPT = ("S-1-5-18", "S-1-5-32-544")  # SYSTEM and Administrators, beside the running account
_GRANTS = frozenset({"A", "OA", "XA", "ZA"})  # the entry types that allow something
# Reparse points that lead elsewhere. Others, such as a cloud file's placeholder, are
# the file itself.
_LINKS = frozenset(
    {stat.IO_REPARSE_TAG_SYMLINK, stat.IO_REPARSE_TAG_MOUNT_POINT, stat.IO_REPARSE_TAG_APPEXECLINK}
)
# Folders Windows and other programs keep their own things in: locked as her data folder,
# their other owners' entries would go, and a repair can't be undone.
_SHARED = (
    *("LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PUBLIC", "SystemRoot", "ProgramFiles"),
    *("ProgramFiles(x86)", "ProgramW6432", "OneDrive", "OneDriveConsumer", "TEMP", "TMP"),
)
_HOME_FOLDERS = ("AppData", "Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")

# Folders already checked by this process: tests start the app many times over.
_checked: set[Path] = set()


@dataclass(frozen=True, slots=True)
class Kept:
    """What `keep_private` did: an outcome, or "failed" with the error's type."""

    outcome: Outcome | Literal["failed"]
    error: str | None = None


def is_private(path: Path, *, top: bool = True) -> bool:
    """Whether only this account, SYSTEM and Administrators own `path` and are granted
    anything on it; at the top, inheritance must be off and this account granted too.
    A folder with no DACL at all is open to all."""
    user = current_user_sid()
    allowed = {user, *_KEPT}
    found = security(path)
    if "NO_ACCESS_CONTROL" in found.head or _sid(found.owner) not in allowed:
        return False
    granted = {_sid(sid) for kind, _flags, sid in found.entries if kind in _GRANTS}
    return granted <= allowed and (not top or ("P" in found.head and user in granted))


def _sid(name: str) -> str:
    """SDDL writes some SIDs as an alias: SY, BA, and LA for the built-in Administrator
    account, which may be the one she runs as."""
    return name if name.startswith("S-") else full_sid(name)


def _lock(path: Path) -> None:
    """Make `path` private, then everything in it that isn't. It goes on past an entry it
    can't change and fails at the end: stopping at the first left the rest open, and the
    next start, finding the top private, called them fine."""
    user = current_user_sid()
    set_dacl(path, "".join(f"(A;OICI;FA;;;{sid})" for sid in (user, *_KEPT)), protected=True)
    _take(path, user)
    failed = 0
    for entry, kind in _tree(path):
        try:
            if kind == "unreadable":
                failed += 1
            elif not is_private(entry, top=False):
                set_dacl(entry, "", protected=False)  # nothing of its own: its folder's entries
                _take(entry, user)
        except (DataAclError, OSError):
            failed += 1
    if failed:
        raise DataAclError(f"{failed} entries could not be made private")


def _take(path: Path, user: str) -> None:
    """Own `path` if another account does: an owner can always change its permissions."""
    if _sid(security(path).owner) not in {user, *_KEPT}:
        set_owner(path, user)


def _tree(top: Path) -> Iterator[tuple[Path, _Kind]]:
    """Everything under `top`, each folder before what it holds, with a folder that can't
    be listed as unreadable. Never through a link or a junction, which lead to things
    that aren't hers (`os.walk` goes through junctions), nor into a file with a second
    name elsewhere, whose permissions the other name shares."""
    stack = [native(top)]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as listing:
                entries = [entry.path for entry in listing]
        except OSError:
            yield Path(folder), "unreadable"
            continue
        for entry in entries:
            kind = _kind(entry)
            if kind != "elsewhere":
                yield Path(entry), kind
            if kind == "folder":
                stack.append(entry)


def _kind(path: str) -> _Kind:
    try:
        info = os.lstat(path)
    except OSError:
        return "unreadable"
    attributes: int = getattr(info, "st_file_attributes", 0)
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT and info.st_reparse_tag in _LINKS:
        return "elsewhere"
    if stat.S_ISDIR(info.st_mode):
        return "folder"
    return "elsewhere" if info.st_nlink > 1 else "file"


def too_wide(path: Path) -> bool:
    """Whether `path` holds more than her data: a drive's root, the installation, the home
    folder, a folder Windows or other programs keep their own things in, or a folder
    above one of them. Locked, it would take the rest with it."""
    real = path.resolve()
    guarded = [INSTALL_ROOT, *_shared_folders()]
    return real == real.parent or any(real in (folder, *folder.parents) for folder in guarded)


def _shared_folders() -> list[Path]:
    home = Path.home()
    folders = [home, *(home / name for name in _HOME_FOLDERS)]
    folders += [Path(value) for name in _SHARED if (value := os.environ.get(name))]
    return [folder.resolve() for folder in folders]


def ensure(path: Path) -> Outcome:
    """Make `path` and everything in it private if they aren't; what was done, for the
    black box. The whole tree is checked: a file moved in or restored keeps permissions
    of its own, at any depth."""
    if too_wide(path):
        return "refused"
    created = not path.is_dir()
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        changed = stat.S_IMODE(path.stat().st_mode) != 0o700
        os.chmod(path, 0o700)
        return "created" if created else "repaired" if changed else "ok"
    if not keeps_permissions(path):
        return "unsupported"
    if (
        not created
        and _private(path, top=True)
        and all(kind != "unreadable" and _private(entry) for entry, kind in _tree(path))
    ):
        return "ok"
    _lock(path)
    return "created" if created else "repaired"


def _private(path: Path, *, top: bool = False) -> bool:
    """`is_private`, counting permissions that can't be read as not: the repair then
    says whether it could put them right."""
    try:
        return is_private(path, top=top)
    except DataAclError:
        return False


async def keep_private(path: Path) -> Kept | None:
    """`ensure` once per process, off the event loop; None if this process already did.

    It runs before the black box starts, whose files may be inside the folder, so it
    records nothing itself: `record` does, once the black box is up. Never raises: a
    folder whose permissions can't be read or set still holds her data, and a start
    that failed on it would lose her altogether.
    """
    try:
        real = path.resolve()
        if real in _checked:
            return None
        outcome = await asyncio.to_thread(ensure, real)
    except Exception as exc:
        return Kept("failed", type(exc).__name__)
    _checked.add(real)
    return Kept(outcome)


def record(kept: Kept | None) -> None:
    """Log what `keep_private` did and put it in the black box. Only an error's type is
    kept: its message can carry the folder's full path."""
    if kept is None:
        return
    if kept.outcome == "failed":
        logger.warning("could not make the data folder private to this account (%s)", kept.error)
        current_blackbox().record("warn", "data.private", outcome="failed", error=kept.error)
        return
    if kept.outcome == "refused":
        logger.warning("the data folder setting holds more than her data, so it was left as is")
    elif kept.outcome == "unsupported":
        logger.warning("the data folder's drive keeps no permissions, so it was left as is")
    elif kept.outcome == "repaired":
        logger.info("made the data folder private to this account again")
    level: Level = "info" if kept.outcome in ("ok", "created") else "warn"
    current_blackbox().record(level, "data.private", outcome=kept.outcome)
