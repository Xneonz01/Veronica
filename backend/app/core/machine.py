"""What this computer has: its NVIDIA card, its memory, and the free space on a disk.

Read for the check in Settings, and for `scripts/diagnostics.py --doctor` (`app/doctor.py`),
and the card's memory in use for a call's room on it (`voice/realtime/card_room.py`).
Only reads: nothing here creates a folder or starts CUDA. torch would answer the card's
memory too, but the first question makes a CUDA context, hundreds of MB of the card for
as long as the process runs; `nvidia-smi` answers from the driver and exits.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

MIB = 1_048_576
_SMI_TIMEOUT_S = 10.0
# nvidia-smi's exit codes for no card to use: 6, no device (measured: "No devices were
# found"), 9, the driver isn't loaded, and 12, its library can't be found (both per
# nvidia-smi's documentation). Any other failure is a card that answered badly: unknown.
_NO_CARD = frozenset({6, 9, 12})
# Where the drivers install it, looked in before PATH: on Windows a PATH lookup tries the
# working folder first, and older drivers leave it off PATH altogether.
_WINDOWS_PLACES = (("SystemRoot", "System32"), ("ProgramFiles", "NVIDIA Corporation/NVSMI"))


class ReadingError(Exception):
    """Something answered, in a shape this doesn't understand, or not in time: unknown,
    not absent."""


@dataclass(frozen=True, slots=True)
class Card:
    """An NVIDIA card as its driver names it, and its memory."""

    name: str
    total_mib: int


@dataclass(frozen=True, slots=True)
class CardUse:
    """How much of the card's memory is in use now, every program's share counted."""

    used_mib: int
    total_mib: int


def nvidia_card() -> Card | None:
    """The card with the most memory among those `nvidia-smi` lists; None when no NVIDIA
    driver answers. With several, bus order says nothing about which one holds her
    models, and the biggest is the one whose size decides whether a call fits."""
    rows = _query("name,memory.total")
    if not rows:
        return None
    return max((_card(row) for row in rows), key=lambda card: card.total_mib)


def card_use() -> CardUse | None:
    """The card's memory in use now, as its driver counts it: what another program holds
    idle included, which a process's own CUDA reading leaves out on Windows (bug 46).
    None when no NVIDIA driver answers, and when there is more than one card: which of
    them holds her models isn't known from here."""
    rows = _query("memory.used,memory.total")
    if not rows or len(rows) > 1:
        return None
    used, _, total = rows[0].partition(",")
    return CardUse(_mib(used, "the memory in use"), _mib(total, "the card's memory"))


def _query(fields: str) -> list[str] | None:
    """nvidia-smi's rows for `fields`, a card each; None when no NVIDIA driver answers."""
    tool = _smi()
    if tool is None:
        return None
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(
            [tool, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SMI_TIMEOUT_S,
            check=False,
            creationflags=flags,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        raise ReadingError(f"nvidia-smi didn't answer in {_SMI_TIMEOUT_S:g} s") from None
    except (OSError, subprocess.SubprocessError) as error:
        raise ReadingError(f"nvidia-smi couldn't run: {type(error).__name__}") from None
    if done.returncode in _NO_CARD:
        return None
    if done.returncode != 0:
        raise ReadingError(f"nvidia-smi exited with {done.returncode}")
    return done.stdout.strip().splitlines()


def _smi() -> str | None:
    if sys.platform == "win32":
        for variable, folder in _WINDOWS_PLACES:
            root = os.environ.get(variable)
            if root and (candidate := Path(root) / folder / "nvidia-smi.exe").is_file():
                return str(candidate)
    return shutil.which("nvidia-smi")


def _card(row: str) -> Card:
    name, _, total = row.rpartition(",")
    total_mib = _mib(total, "the card's memory")
    if not name.strip():
        raise ReadingError("nvidia-smi gave no name for the card")
    return Card(name.strip(), total_mib)


def _mib(text: str, what: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise ReadingError(f"nvidia-smi gave {what} as {text.strip()!r}") from None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def total_memory_mib() -> int:
    """The computer's memory (RAM) in MiB. ReadingError where it can't be read, so the
    reason is logged rather than lost."""
    try:
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            query = kernel32.GlobalMemoryStatusEx
            query.argtypes = [ctypes.POINTER(_MemoryStatus)]
            query.restype = wintypes.BOOL
            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if not query(ctypes.byref(status)):
                raise ReadingError(f"GlobalMemoryStatusEx failed ({ctypes.get_last_error()})")
            total: int = status.ullTotalPhys
        else:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError) as error:
        raise ReadingError(f"the memory size couldn't be read: {type(error).__name__}") from None
    if total <= 0:
        raise ReadingError(f"the memory size read as {total}")
    return total // MIB


def free_mib(path: Path) -> int:
    """Free space, in whole MiB rounded down, on the disk that holds `path` or would:
    asked of the nearest folder that exists, since free space belongs to the disk, and a
    folder made only to ask would be left behind."""
    folder = path
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return shutil.disk_usage(folder).free // MIB
