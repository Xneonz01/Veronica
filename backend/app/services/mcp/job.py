"""A Windows job object that holds a tool server and everything it starts, so none of
it can outlive her backend.

The job is made with `KILL_ON_JOB_CLOSE`: closing its handle, which Windows also does
when her backend exits for any reason, a crash included, ends every process in it. A
server started normally could start a child before it was assigned to the job, and that
child would escape it; so the server starts suspended, is assigned, and only then are
its threads resumed (`CREATE_SUSPENDED` gives no handle to its first thread through
`subprocess`, so they are found with a Toolhelp snapshot of the system's threads).

ctypes bindings on a kernel32 loaded for this module alone, as `core/win_security.py`
does, so the argument types declared here never change `ctypes.windll` elsewhere. Only
documented APIs.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import cache
from typing import Any

CREATE_SUSPENDED = 0x0000_0004
_KILL_ON_JOB_CLOSE = 0x0000_2000
_EXTENDED_LIMITS = 9  # JobObjectExtendedLimitInformation
_PROCESS_RIGHTS = 0x0001 | 0x0100  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
_THREAD_SUSPEND_RESUME = 0x0002
_SNAP_THREADS = 0x0000_0004  # TH32CS_SNAPTHREAD
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class JobError(OSError):
    """The server couldn't be held in a job; it is stopped rather than run loose."""


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


@cache
def _kernel32() -> Any:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, dword, boolean = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    signatures: list[tuple[str, list[Any], Any]] = [
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], handle),
        ("SetInformationJobObject", [handle, ctypes.c_int, ctypes.c_void_p, dword], boolean),
        ("AssignProcessToJobObject", [handle, handle], boolean),
        ("OpenProcess", [dword, boolean, dword], handle),
        ("OpenThread", [dword, boolean, dword], handle),
        ("ResumeThread", [handle], dword),
        ("CreateToolhelp32Snapshot", [dword, dword], handle),
        ("Thread32First", [handle, ctypes.POINTER(_ThreadEntry)], boolean),
        ("Thread32Next", [handle, ctypes.POINTER(_ThreadEntry)], boolean),
        ("CloseHandle", [handle], boolean),
    ]
    for name, args, result in signatures:
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    return kernel


def _failed(what: str) -> JobError:
    return JobError(f"{what} (error {ctypes.get_last_error()})")


class Job:
    """One job, holding one server's processes. `close` ends them all."""

    def __init__(self) -> None:
        kernel = _kernel32()
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise _failed("CreateJobObjectW")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        size = ctypes.sizeof(limits)
        if not kernel.SetInformationJobObject(
            self._handle, _EXTENDED_LIMITS, ctypes.byref(limits), size
        ):
            self.close()
            raise _failed("SetInformationJobObject")

    def hold(self, pid: int) -> None:
        """Assign the suspended process `pid`, then resume its threads."""
        kernel = _kernel32()
        process = kernel.OpenProcess(_PROCESS_RIGHTS, False, pid)
        if not process:
            raise _failed("OpenProcess")
        try:
            if not kernel.AssignProcessToJobObject(self._handle, process):
                raise _failed("AssignProcessToJobObject")
        finally:
            kernel.CloseHandle(process)
        if resume(pid) == 0:
            raise JobError("the server's first thread wasn't found to resume")

    def close(self) -> None:
        """End every process in the job (`KILL_ON_JOB_CLOSE`). Safe to call twice."""
        if self._handle:
            _kernel32().CloseHandle(self._handle)
            self._handle = None


def resume(pid: int) -> int:
    """Resume every thread of process `pid`; how many were resumed."""
    kernel = _kernel32()
    snapshot = kernel.CreateToolhelp32Snapshot(_SNAP_THREADS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        raise _failed("CreateToolhelp32Snapshot")
    resumed = 0
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == pid:
                thread = kernel.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    if kernel.ResumeThread(thread) != 0xFFFF_FFFF:
                        resumed += 1
                    kernel.CloseHandle(thread)
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return resumed
