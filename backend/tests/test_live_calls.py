"""Letting the speech models go after the last call, and not while one could still use them."""

from __future__ import annotations

import asyncio
import gc
import threading

from app.core.config import Settings
from app.core.event_bus import InProcessEventBus
from app.core.gpu_manager import GpuMode
from app.services.voice.calls import LiveCalls
from app.services.voice.realtime.session import RealtimeSession
from tests.fakes import (
    FakeDetector,
    FakeLLM,
    FakeSTT,
    FakeTTS,
    FakeWS,
    ListMetricsSink,
    build_fake_runtime,
)
from tests.test_realtime_session import wait_until


class _Manager:
    """Records the modes it is switched to; can fail to load, as a full card would."""

    def __init__(self, *, fail_to_load: bool = False) -> None:
        self.modes: list[GpuMode] = []
        self.loads: list[bool] = []
        self._fail_to_load = fail_to_load

    async def set_mode(self, mode: GpuMode, *, load: bool = True) -> None:
        self.modes.append(mode)
        self.loads.append(load)
        if self._fail_to_load and mode is GpuMode.conversation:
            raise RuntimeError("out of memory")


class _Call:
    def __init__(self) -> None:
        self.call_active = True


async def test_the_models_go_a_while_after_the_last_call_ends() -> None:
    manager = _Manager()
    calls = LiveCalls(manager)  # type: ignore[arg-type]
    call = _Call()
    calls.started(call)

    call.call_active = False
    calls.ended(0.05)
    await asyncio.sleep(0.01)
    assert manager.modes == []  # not yet: a call may start straight back

    await wait_until(lambda: manager.modes == [GpuMode.idle])


async def test_a_voice_preview_outside_a_call_lets_the_model_go_too() -> None:
    """Bug 13: a preview outside a call loaded the voice model, which stayed until the
    next call ended; during a call, the call keeps it (mutations: never release; release
    under a live call)."""
    manager = _Manager()
    calls = LiveCalls(manager)  # type: ignore[arg-type]
    calls.release_later(0.02)
    await wait_until(lambda: manager.modes == [GpuMode.idle])
    assert manager.loads == [False]  # letting go only

    during = _Manager()
    in_call = LiveCalls(during)  # type: ignore[arg-type]
    live = _Call()  # held: the calls are held weakly
    in_call.started(live)
    in_call.release_later(0.0)
    await asyncio.sleep(0.02)
    assert during.modes == [] and live.call_active


async def test_a_call_started_inside_the_window_keeps_them() -> None:
    manager = _Manager()
    calls = LiveCalls(manager)  # type: ignore[arg-type]
    calls.ended(0.02)

    calls.started(_Call())
    await asyncio.sleep(0.06)

    assert manager.modes == []


async def test_they_stay_while_another_call_is_live() -> None:
    manager = _Manager()
    calls = LiveCalls(manager)  # type: ignore[arg-type]
    first, second = _Call(), _Call()
    calls.started(first)
    calls.started(second)

    first.call_active = False
    calls.ended(0.0)
    await asyncio.sleep(0.02)

    assert manager.modes == []


def test_a_call_counts_only_while_it_is_live_and_while_it_exists() -> None:
    # Read off each call, not counted up and down: a failed warm-up drops the flag
    # without a stop, and a session that is gone must not count as a live call forever.
    calls = LiveCalls(_Manager())  # type: ignore[arg-type]
    call = _Call()
    calls.started(call)
    assert calls.count() == 1

    call.call_active = False
    assert calls.count() == 0

    gone = _Call()
    calls.started(gone)
    del gone
    gc.collect()
    assert calls.count() == 0


async def test_shutdown_calls_off_a_pending_release() -> None:
    manager = _Manager()
    calls = LiveCalls(manager)  # type: ignore[arg-type]
    calls.ended(0.02)

    calls.close()
    await asyncio.sleep(0.05)

    assert manager.modes == []


# --- through a call's own start and stop -----------------------------------------------


def _session(manager: _Manager) -> RealtimeSession:
    settings = Settings()
    # From the section as placed: a new one would keep its own metrics location.
    settings.realtime = settings.realtime.model_copy(update={"release_models_after_s": 0.01})
    runtime = build_fake_runtime(
        stt=FakeSTT([], None),
        tts=FakeTTS(),
        llm=FakeLLM([]),
        manager=manager,  # type: ignore[arg-type]
    )
    return RealtimeSession(
        FakeWS(),  # type: ignore[arg-type]
        runtime,
        InProcessEventBus(),
        settings,
        persona=lambda: "You are Veronica.",
        metrics_sink=ListMetricsSink(),  # type: ignore[arg-type]
        detector=FakeDetector([]),
    )


async def test_ending_a_call_releases_the_speech_models() -> None:
    manager = _Manager()
    session = _session(manager)
    await session._on_call_start()
    await wait_until(lambda: session._ingest_task is not None)
    assert manager.modes == [GpuMode.conversation]

    await session._stop_call()

    await wait_until(lambda: manager.modes == [GpuMode.conversation, GpuMode.idle])


async def test_a_call_whose_warm_up_failed_releases_them_too() -> None:
    # The models may be half loaded when the warm-up fails, and the call never reaches
    # `stop`: it ends there, so the release has to be scheduled there too.
    manager = _Manager(fail_to_load=True)
    session = _session(manager)

    await session._on_call_start()

    await wait_until(lambda: manager.modes == [GpuMode.conversation, GpuMode.idle])
    assert session.call_active is False


async def test_a_stopped_call_leaves_no_detection_thread_behind() -> None:
    session = _session(_Manager())
    await session._on_call_start()
    await wait_until(lambda: session._ingest_task is not None)
    worker = session.vad_worker
    assert worker is not None
    thread = next(t for t in threading.enumerate() if t is worker._thread)

    await session._stop_call()

    assert session.vad_worker is None
    assert not thread.is_alive()


async def test_the_release_only_lets_go() -> None:
    manager = _Manager()
    calls = LiveCalls(manager)  # type: ignore[arg-type]
    calls.ended(0.0)

    await wait_until(lambda: manager.modes == [GpuMode.idle])

    assert manager.loads == [False]
