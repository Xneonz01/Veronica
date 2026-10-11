"""What one process holds on the graphics card, by Windows' own count.

On Windows the driver keeps each program's share to itself: nvidia-smi lists processes
with "[N/A]" for their memory, and a process's own CUDA reading doesn't count what
another program holds idle (bug 46). Windows counts it per process, for Task Manager, as
the "GPU Process Memory" performance counters, read here through PDH, their API.
Measured on 0.34.5: a process holding 2 GiB read 2,267 MiB, exactly what nvidia-smi's
figure for the card rose by when it started (its CUDA context is the rest).

Only reads: a query is opened, collected once and closed.
"""

from __future__ import annotations

import ctypes
import functools
import re
import sys
from ctypes import wintypes
from typing import Any

from app.core.machine import MIB, ReadingError

# In the card's own memory. "Shared Usage" is system memory the card borrows, where a
# full card's allocations spill.
_COUNTER = r"\GPU Process Memory(*)\Dedicated Usage"
_FMT_LARGE = 0x00000400  # PDH_FMT_LARGE: the value as a 64-bit integer, in bytes here
_MORE_DATA = 0x800007D2  # PDH_MORE_DATA: the buffer given was too small
_VALID = frozenset({0, 1})  # PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA
_TRIES = 3  # processes come and go between sizing the answer and reading it
# One instance per process and adapter: "pid_1234_luid_0x00000000_0x0000D1F2_phys_0".
_INSTANCE = re.compile(r"pid_(\d+)_")


class _Value(ctypes.Structure):
    # PDH_FMT_COUNTERVALUE: a status, then a union whose widest members take 8 bytes;
    # a c_longlong gets the same alignment in ctypes as the union has in C.
    _fields_ = [("status", wintypes.DWORD), ("large", ctypes.c_longlong)]


class _Item(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("value", _Value)]  # PDH_FMT_COUNTERVALUE_ITEM_W


def held_mib(pid: int) -> int | None:
    """What process `pid` holds in the graphics card's memory now, in MiB rounded down,
    over every adapter it uses; 0 when it holds nothing there. None off Windows, which
    doesn't count it this way; ReadingError when it can't be read."""
    if sys.platform != "win32":
        return None
    held = sum(value for owner, value in _dedicated() if owner == pid)
    return held // MIB


def _dedicated() -> list[tuple[int, int]]:
    """(process id, bytes) for every process on every adapter."""
    pdh = _pdh()
    query = wintypes.HANDLE()
    _check(pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)), "open a query")
    try:
        counter = wintypes.HANDLE()
        _check(
            pdh.PdhAddEnglishCounterW(query, _COUNTER, 0, ctypes.byref(counter)),
            "find the GPU memory counters",
        )
        _check(pdh.PdhCollectQueryData(query), "collect the GPU memory counters")
        return _values(pdh, counter)
    finally:
        pdh.PdhCloseQuery(query)


def _values(pdh: Any, counter: wintypes.HANDLE) -> list[tuple[int, int]]:
    for _ in range(_TRIES):
        # Sized afresh each try: after a short buffer, the size it reports isn't one to
        # rely on (PdhGetFormattedCounterArray's documentation).
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(
            counter, _FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None
        )
        if status != _MORE_DATA:
            _check(status, "size the GPU memory counters")
            return []  # asked with no room and nothing more to give: no instances
        buffer = ctypes.create_string_buffer(size.value)
        status = pdh.PdhGetFormattedCounterArrayW(
            counter, _FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buffer
        )
        if status == _MORE_DATA:
            continue
        _check(status, "read the GPU memory counters")
        items = ctypes.cast(buffer, ctypes.POINTER(_Item * count.value)).contents
        return [
            (int(match.group(1)), item.value.large)
            for item in items
            if item.value.status in _VALID and (match := _INSTANCE.match(item.name or ""))
        ]
    raise ReadingError("the GPU memory counters kept changing while being read")


def _check(status: int, what: str) -> None:
    if status != 0:
        raise ReadingError(f"Windows couldn't {what} (PDH status 0x{status:08X})")


@functools.cache
def _pdh() -> Any:
    """pdh.dll with the prototypes this reads by: without them ctypes would pass the
    handles as 32-bit ints on 64-bit Windows."""
    pdh = ctypes.WinDLL("pdh")
    handle = ctypes.POINTER(wintypes.HANDLE)
    status = wintypes.DWORD  # PDH_STATUS is a LONG; unsigned compares with the codes above
    pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, handle]
    pdh.PdhOpenQueryW.restype = status
    pdh.PdhAddEnglishCounterW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        ctypes.c_size_t,
        handle,
    ]
    pdh.PdhAddEnglishCounterW.restype = status
    pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
    pdh.PdhCollectQueryData.restype = status
    pdh.PdhGetFormattedCounterArrayW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    pdh.PdhGetFormattedCounterArrayW.restype = status
    pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
    pdh.PdhCloseQuery.restype = status
    return pdh
