"""`until_out`: a synthesis thread stays its caller's until it is out of the model.

`test_tts.py` covers the ordinary case through the adapter; these are the edges: a
thread left behind by the loop shutting down, and a fault in a sentence nobody waited
for.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

import pytest

from app.adapters.tts import interruptible


def _polling_work(
    started: threading.Event, stopped: threading.Event, error: Exception
) -> Callable[[], bytes]:
    """A synthesis that checks the flag every 10 ms, as the model checks it every token."""

    def work() -> bytes:
        started.set()
        for _ in range(500):
            if interruptible.ABANDON.is_set():
                stopped.set()
                raise error
            time.sleep(0.01)
        return b""

    return work


async def test_a_thread_left_behind_by_shutdown_is_still_told_to_stop() -> None:
    """Shutting the loop down cancels the thread's own task along with its caller.

    The flag was cleared on the way out regardless, so the thread went on to the end of
    its sentence, and the process waited for it before it could exit.
    """
    started, stopped = threading.Event(), threading.Event()
    work = _polling_work(started, stopped, interruptible.SynthesisAbandoned())
    caller = asyncio.create_task(interruptible.until_out(work))
    assert await asyncio.to_thread(started.wait, 5.0)
    threads = [task for task in asyncio.all_tasks() if "to_thread" in repr(task.get_coro())]
    for task in (*threads, caller):
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    try:
        assert await asyncio.to_thread(stopped.wait, 2.0)
    finally:
        interruptible.ABANDON.clear()


async def test_a_fault_in_an_abandoned_sentence_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Stopping by design raises `SynthesisAbandoned`; anything else was dropped unread.
    started, stopped = threading.Event(), threading.Event()
    work = _polling_work(started, stopped, RuntimeError("CUDA error: device-side assert"))
    caller = asyncio.create_task(interruptible.until_out(work))
    assert await asyncio.to_thread(started.wait, 5.0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert "device-side assert" in caplog.text
    assert not interruptible.ABANDON.is_set()  # cleared for the next sentence
