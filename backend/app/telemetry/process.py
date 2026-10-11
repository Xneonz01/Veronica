"""Process readings for the black box, without adding a psutil dependency.

Taken at each call boundary: memory, threads or handles that climb from one call to
the next are how a per-call leak shows up.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def cuda_used_mb() -> int | None:
    """VRAM in use on the default GPU in MB, or None if CUDA isn't initialised yet.

    It never initialises CUDA itself: creating a context just to take a reading would
    cost hundreds of MB of VRAM and a noticeable pause.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return None
    try:
        if not torch.cuda.is_initialized():
            return None
        free, total = torch.cuda.mem_get_info()
        return round(int(total - free) / 1_048_576)
    except Exception:
        return None


def _windows_working_set() -> int | None:
    # The prototypes must be declared: without them ctypes passes the process handle
    # as a 32-bit int, and on 64-bit Windows the call fails.
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    query = kernel32.K32GetProcessMemoryInfo
    query.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    query.restype = wintypes.BOOL
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not query(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    working_set: int = counters.WorkingSetSize
    return working_set


def rss_mb() -> int | None:
    """Resident memory of this process in MB, or None where it can't be read.

    None rather than 0: a zero would read as a real measurement.
    """
    try:
        if sys.platform == "win32":
            working_set = _windows_working_set()
            return None if working_set is None else round(working_set / 1_048_576)
        import resource

        # ru_maxrss is the peak, in KB on Linux and bytes on macOS.
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1_048_576 if sys.platform == "darwin" else 1024))
    except Exception:
        return None


def thread_count() -> int:
    """Live Python threads (native threads inside torch or CTranslate2 aren't counted)."""
    return threading.active_count()


def _windows_handle_count() -> int | None:
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    query = kernel32.GetProcessHandleCount
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not query(kernel32.GetCurrentProcess(), ctypes.byref(count)):
        return None
    return int(count.value)


def handle_count() -> int | None:
    """Open OS handles on Windows, open file descriptors on Linux; None elsewhere."""
    try:
        if sys.platform == "win32":
            return _windows_handle_count()
        if os.path.isdir("/proc/self/fd"):
            return len(os.listdir("/proc/self/fd"))
        return None
    except Exception:
        return None
