"""Voice detection on its own thread: order, failures, shutdown and dating."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import numpy as np
import pytest

from app.services.voice.turn import TurnEvent
from app.services.voice.vad_worker import VadResult, VadWorker


class _Detector:
    """Tags each frame with the order it was scored in; can fail on chosen frames."""

    def __init__(self, fail_on: set[int] | None = None, delay_s: float = 0.0) -> None:
        self.seen = 0
        self.threads: set[str] = set()
        self._fail_on = fail_on or set()
        self._delay_s = delay_s

    def process_audio(self, samples: Any) -> list[TurnEvent]:
        self.threads.add(threading.current_thread().name)
        self.seen += 1
        if self._delay_s:
            time.sleep(self._delay_s)  # a slow model, on the detector's own thread
        if self.seen in self._fail_on:
            raise RuntimeError("bad frame")
        assert samples.dtype == np.float32
        return [TurnEvent.speech_end] if self.seen % 3 == 0 else []

    def trailing_silence_ms(self) -> float:
        return float(self.seen)


def _frame(n: int) -> bytes:
    return n.to_bytes(2, "little") * 320  # 20 ms of PCM16, recognisable by its value


async def _results(worker: VadWorker, count: int) -> list[VadResult]:
    got: list[VadResult] = []
    while len(got) < count:
        item = await asyncio.wait_for(worker.results.get(), timeout=2.0)
        assert item is not None
        got.append(item)
    return got


async def test_every_frame_comes_back_once_in_order_with_its_events() -> None:
    detector = _Detector()
    worker = VadWorker(detector, time.perf_counter)
    try:
        for n in range(60):  # a burst, faster than the thread scores them
            worker.submit(_frame(n))
        got = await _results(worker, 60)
    finally:
        await asyncio.to_thread(worker.close)

    assert [item.frame for item in got] == [_frame(n) for n in range(60)]
    assert [bool(item.events) for item in got] == [(n + 1) % 3 == 0 for n in range(60)]
    assert detector.threads == {"vad-worker"}  # never the event loop's thread


async def test_a_frame_the_detector_fails_on_is_passed_on_without_events() -> None:
    worker = VadWorker(_Detector(fail_on={2, 3}), time.perf_counter)
    try:
        for n in range(5):
            worker.submit(_frame(n))
        got = await _results(worker, 5)
    finally:
        await asyncio.to_thread(worker.close)

    assert [item.frame for item in got] == [_frame(n) for n in range(5)]  # still alive
    assert got[1].events == [] and got[2].events == []
    assert worker.timing()["vad_failed"] == 2


async def test_a_failing_detector_is_reported_once_not_every_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = VadWorker(_Detector(fail_on=set(range(1, 11))), time.perf_counter)
    with caplog.at_level(logging.WARNING):
        for n in range(10):
            worker.submit(_frame(n))
        await _results(worker, 10)
        await asyncio.to_thread(worker.close)

    assert sum("voice detection failed" in record.message for record in caplog.records) == 1


async def test_the_end_of_speech_is_dated_on_the_thread_that_scored_it() -> None:
    readings = iter([10.0, 11.0, 12.0])
    worker = VadWorker(_Detector(), lambda: next(readings))
    try:
        for n in range(3):
            worker.submit(_frame(n))
        got = await _results(worker, 3)
    finally:
        await asyncio.to_thread(worker.close)

    ended = got[2]
    assert ended.events == [TurnEvent.speech_end]
    assert (ended.scored_at, ended.trailing_silence_ms) == (12.0, 3.0)


async def test_closing_stops_the_thread_and_wakes_the_reader() -> None:
    worker = VadWorker(_Detector(delay_s=0.01), time.perf_counter)
    for n in range(200):  # a backlog the thread won't get through before the close
        worker.submit(_frame(n))
    reader = asyncio.ensure_future(_drain(worker))

    started = time.perf_counter()
    await asyncio.to_thread(worker.close)

    assert time.perf_counter() - started < 1.0  # didn't work through the backlog first
    assert not worker._thread.is_alive()
    await asyncio.wait_for(reader, timeout=2.0)  # the reader saw the end, not a hang
    queued = worker._inbox.qsize()
    worker.submit(_frame(0))  # after the close: ignored, not queued for nobody
    assert worker._inbox.qsize() == queued


async def _drain(worker: VadWorker) -> None:
    while await worker.results.get() is not None:
        pass


async def test_a_full_inbox_drops_frames_and_counts_them() -> None:
    gate = threading.Event()

    class _Stuck(_Detector):
        def process_audio(self, samples: Any) -> list[TurnEvent]:
            gate.wait(timeout=5.0)
            return []

    worker = VadWorker(_Stuck(), time.perf_counter)
    try:
        for n in range(1100):  # more than the inbox holds while the thread is stuck
            worker.submit(_frame(n % 100))
        assert worker.dropped > 0
    finally:
        gate.set()
        await asyncio.to_thread(worker.close)
    assert worker.timing()["vad_dropped"] == worker.dropped
