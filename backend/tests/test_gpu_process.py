"""What a process holds on the graphics card, by Windows' "GPU Process Memory" counters
(bug 46): summed over its adapters, read from PDH's answer as it lays it out, and never
guessed when it can't be read."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Any

import pytest

from app.core import gpu_process
from app.core.machine import MIB, ReadingError

GIB = 1024 * MIB


def test_a_process_share_is_summed_over_its_adapters_in_whole_mib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu_process.sys, "platform", "win32")
    shares = [(10, GIB), (10, 5 * MIB + MIB - 1), (11, 3 * GIB)]
    monkeypatch.setattr(gpu_process, "_dedicated", lambda: shares)
    assert gpu_process.held_mib(10) == 1029  # rounded down
    assert gpu_process.held_mib(12) == 0  # nothing on the card


def test_off_windows_there_is_no_such_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_process.sys, "platform", "linux")

    def unread() -> list[tuple[int, int]]:
        raise AssertionError("PDH is Windows'")

    monkeypatch.setattr(gpu_process, "_dedicated", unread)
    assert gpu_process.held_mib(10) is None


class _Pdh:
    """PdhGetFormattedCounterArrayW answering as PDH does: the size first, for a call
    with no buffer, then the items laid out in the buffer, their names after them."""

    def __init__(self, entries: list[tuple[str, int, int]], *, changing: bool = False) -> None:
        self.names = [ctypes.create_unicode_buffer(name) for name, _, _ in entries]
        self.items = (gpu_process._Item * len(entries))()
        for item, name, (_, status, value) in zip(self.items, self.names, entries, strict=True):
            item.name = ctypes.cast(name, wintypes.LPWSTR)
            item.value.status = status
            item.value.large = value
        self.changing = changing
        self.asked = 0

    def PdhGetFormattedCounterArrayW(  # noqa: N802 - PDH's own name
        self, _counter: Any, fmt: int, size: Any, count: Any, buffer: Any
    ) -> int:
        assert fmt == gpu_process._FMT_LARGE
        self.asked += 1
        if buffer is None or self.changing:
            size._obj.value = ctypes.sizeof(self.items)  # the DWORD behind byref()
            count._obj.value = len(self.items)
            return gpu_process._MORE_DATA
        ctypes.memmove(buffer, ctypes.addressof(self.items), ctypes.sizeof(self.items))
        return 0


def test_the_answer_is_read_as_pdh_lays_it_out_and_only_valid_values_count() -> None:
    pdh = _Pdh(
        [
            ("pid_10_luid_0x00000000_0x0000D1F2_phys_0", 0, GIB),
            ("pid_10_luid_0x00000000_0x0000A001_phys_0", 1, 5 * MIB),  # new data: valid
            ("pid_11_luid_0x00000000_0x0000D1F2_phys_0", 0, 3 * GIB),
            ("pid_12_luid_0x00000000_0x0000D1F2_phys_0", 0xC0000BC6, 7 * GIB),  # no data
            ("_Total", 0, 9 * GIB),  # not a process
        ]
    )
    shares = gpu_process._values(pdh, wintypes.HANDLE())
    assert shares == [(10, GIB), (10, 5 * MIB), (11, 3 * GIB)]
    assert pdh.asked == 2


def test_an_answer_that_keeps_changing_is_unreadable_not_empty() -> None:
    pdh = _Pdh([("pid_10_luid_0x0_0x1_phys_0", 0, GIB)], changing=True)
    with pytest.raises(ReadingError, match="kept changing"):
        gpu_process._values(pdh, wintypes.HANDLE())
    assert pdh.asked == 2 * gpu_process._TRIES


def test_a_status_pdh_gives_is_said_in_its_hex_form() -> None:
    gpu_process._check(0, "open a query")  # no error
    with pytest.raises(ReadingError, match=r"find the counters \(PDH status 0xC0000BB8\)"):
        gpu_process._check(0xC0000BB8, "find the counters")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own count")
def test_windows_own_count_is_read() -> None:
    """The counters as this machine has them: a layout read wrong would find no process
    at all, or fail. Without them (no GPU driver that fills them), it's unreadable."""
    try:
        shares = gpu_process._dedicated()
        mine = gpu_process.held_mib(os.getpid())
    except ReadingError:
        pytest.skip("no GPU memory counters on this machine")
    assert shares, "no process at all holds memory on any adapter"
    assert all(pid > 0 and held >= 0 for pid, held in shares)
    assert mine is not None and mine >= 0
