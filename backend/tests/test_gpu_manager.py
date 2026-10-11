"""GPU model manager: lazy load, mode switching, budget enforcement."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

import pytest

from app.core import gpu_manager as gm
from app.core.gpu_ledger import Ledger, ModelCost
from app.core.gpu_manager import CudaGpuModelManager, GpuMode


def _loader(record: list[str], name: str) -> Callable[[], str]:
    def load() -> str:
        record.append(name)
        return f"{name}-model"

    return load


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> CudaGpuModelManager:
    # Make VRAM reporting deterministic (no real CUDA dependency in unit tests).
    monkeypatch.setattr(gm, "_cuda_vram_used_gib", lambda: 0.0)
    monkeypatch.setattr(gm, "_free_cuda_cache", lambda: None)
    return CudaGpuModelManager(budget_gib=16.0)


async def test_lazy_load_then_cached(manager: CudaGpuModelManager) -> None:
    loads: list[str] = []
    manager.register("stt", _loader(loads, "stt"), {GpuMode.conversation}, 2.0)
    assert loads == []  # not loaded until requested
    assert await manager.get("stt") == "stt-model"
    assert await manager.get("stt") == "stt-model"
    assert loads == ["stt"]  # loaded exactly once


async def test_set_mode_loads_modeset_and_unloads_rest(
    manager: CudaGpuModelManager,
) -> None:
    loads: list[str] = []
    manager.register("stt", _loader(loads, "stt"), {GpuMode.conversation}, 2.0)
    manager.register("tts", _loader(loads, "tts"), {GpuMode.conversation}, 3.0)
    manager.register("rest", _loader(loads, "rest"), {GpuMode.idle}, 2.0)

    await manager.set_mode(GpuMode.conversation)
    assert set(loads) == {"stt", "tts"}  # the idle one isn't a call's

    await manager.set_mode(GpuMode.idle)
    assert "rest" in loads and not manager.is_loaded("stt")  # its own loaded, a call's gone

    loads.clear()
    await manager.set_mode(GpuMode.conversation)
    assert set(loads) == {"stt", "tts"}  # reloaded after being unloaded


async def test_what_a_mode_would_still_load(manager: CudaGpuModelManager) -> None:
    """A call's failed warm-up names only these (mutation: every registered model)."""
    loads: list[str] = []
    manager.register("stt", _loader(loads, "stt"), {GpuMode.conversation}, 2.0)
    manager.register("tts", _loader(loads, "tts"), {GpuMode.conversation}, 3.0)
    manager.register("rest", _loader(loads, "rest"), {GpuMode.idle}, 2.0)
    manager.register("gone", _loader(loads, "gone"), {GpuMode.conversation}, 1.0)
    manager.unregister("gone")  # a memory file that couldn't be opened
    await manager.get("stt")

    assert manager.to_load(GpuMode.conversation) == {"tts"}
    assert manager.to_load(GpuMode.idle) == {"rest"}


async def test_unregistered_model_raises(manager: CudaGpuModelManager) -> None:
    with pytest.raises(KeyError):
        await manager.ensure_loaded("missing")


async def test_budget_warns_when_tight(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(gm, "_free_cuda_cache", lambda: None)
    monkeypatch.setattr(gm, "_cuda_vram_used_gib", lambda: 15.5)  # nearly full
    manager = CudaGpuModelManager(budget_gib=16.0)
    manager.register("big", _loader([], "big"), {GpuMode.conversation}, 4.0)
    with caplog.at_level(logging.WARNING):
        await manager.ensure_loaded("big")
    assert any("budget tight" in r.message.lower() for r in caplog.records)


async def test_mode_switches_run_one_at_a_time(manager: CudaGpuModelManager) -> None:
    # The release after a call (to idle) and the next call's warm-up (to conversation)
    # can overlap. Interleaved, the warm-up found the voice model still loaded and moved
    # on, and the release then unloaded it: the call started without it.
    loads: list[str] = []
    manager.register("stt", _loader(loads, "stt"), {GpuMode.conversation}, 2.0)
    manager.register("tts", _loader(loads, "tts"), {GpuMode.conversation}, 3.0)
    await manager.set_mode(GpuMode.conversation)

    release = asyncio.create_task(manager.set_mode(GpuMode.idle))
    warm_up = asyncio.create_task(manager.set_mode(GpuMode.conversation))
    await asyncio.gather(release, warm_up)
    loaded = list(loads)

    await manager.get("stt")
    await manager.get("tts")
    assert loads == loaded  # both were loaded by the warm-up; nothing loads on first use


async def test_letting_go_loads_nothing(manager: CudaGpuModelManager) -> None:
    # The release after a call goes to idle mode, which lists the memory model. A call
    # that failed before loading it had it loaded a minute later, by the release.
    loads: list[str] = []
    manager.register("tts", _loader(loads, "tts"), {GpuMode.conversation}, 3.0)
    manager.register(
        "embedding", _loader(loads, "embedding"), {GpuMode.idle, GpuMode.conversation}, 1.5
    )
    await manager.get("tts")  # loaded, as by a call that got that far
    loads.clear()

    await manager.set_mode(GpuMode.idle, load=False)

    assert loads == []  # the memory model stays unloaded
    await manager.get("tts")
    assert loads == ["tts"]  # the voice model was let go, so this loads it again


async def test_a_model_forgotten_before_it_loads_is_never_loaded(
    manager: CudaGpuModelManager,
) -> None:
    """The memory model of a memory file that couldn't be opened: a call's mode switch
    must not load it for nothing (mutation: unregister does nothing)."""
    loads: list[str] = []
    manager.register("stt", _loader(loads, "stt"), {GpuMode.conversation}, 2.0)
    manager.register(
        "embedding", _loader(loads, "embedding"), {GpuMode.idle, GpuMode.conversation}, 1.5
    )

    manager.unregister("embedding")
    await manager.set_mode(GpuMode.conversation)

    assert loads == ["stt"]
    with pytest.raises(KeyError):
        manager.is_loaded("embedding")


async def test_a_loaded_model_is_not_forgotten(manager: CudaGpuModelManager) -> None:
    loads: list[str] = []
    manager.register("stt", _loader(loads, "stt"), {GpuMode.conversation}, 2.0)
    await manager.get("stt")

    manager.unregister("stt")

    assert manager.is_loaded("stt")


async def test_every_reading_and_the_cache_are_taken_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first imports torch and makes the CUDA context, seconds every request would
    wait for; the ones after a release follow the cache handed back (mutation: read on
    the loop)."""
    loop_thread = threading.current_thread()
    readers: list[threading.Thread] = []

    def reading() -> float:
        readers.append(threading.current_thread())
        return 0.0

    def free_cache() -> None:
        readers.append(threading.current_thread())

    monkeypatch.setattr(gm, "_cuda_vram_used_gib", reading)
    monkeypatch.setattr(gm, "_free_cuda_cache", free_cache)
    manager = CudaGpuModelManager(budget_gib=16.0)
    manager.register("stt", _loader([], "stt"), {GpuMode.conversation}, 2.0)

    await manager.ensure_loaded("stt")
    await manager.unload("stt")

    assert len(readers) == 4  # before and after the load, the cache and after the release
    assert all(reader is not loop_thread for reader in readers)


class _Box:
    """A black box that keeps what it is given."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record(self, _level: str, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> _Box:
    recorder = _Box()
    monkeypatch.setattr(gm, "current_blackbox", lambda: recorder)
    return recorder


def test_the_ledger_keeps_each_models_loads_and_their_times() -> None:
    ledger = Ledger()
    ledger.loaded("stt", 100.0)
    ledger.loaded("stt", 300.4)

    costs = ledger.costs({"stt": 2.0, "tts": 3.0}, resident={"stt"})

    assert costs["stt"] == ModelCost(
        loads=2, last_load_ms=300, mean_load_ms=200, vram_estimate_mb=2048, resident=True
    )
    assert costs["tts"] == ModelCost(
        loads=0, last_load_ms=None, mean_load_ms=None, vram_estimate_mb=3072, resident=False
    )


async def test_what_each_model_cost_the_card_is_kept(manager: CudaGpuModelManager) -> None:
    """Reloads count, so thrash shows; a model never needed says so (mutation: a reload
    resets the count)."""
    manager.register("stt", _loader([], "stt"), {GpuMode.conversation}, 2.0)
    manager.register("tts", _loader([], "tts"), {GpuMode.conversation}, 3.0)

    await manager.get("stt")
    await manager.unload("stt")
    await manager.get("stt")

    costs = manager.ledger()
    assert list(costs) == ["stt", "tts"]
    assert costs["stt"].loads == 2 and costs["stt"].resident
    assert costs["tts"].loads == 0 and not costs["tts"].resident


async def test_a_warm_up_cancelled_after_a_load_leaves_the_ledger_right(
    monkeypatch: pytest.MonkeyPatch, box: _Box
) -> None:
    """The model is on the card once its loader returns, whatever happens to the warm-up
    that asked for it (mutation: count the load after the reading that follows it)."""
    reading_started, reading_may_end = threading.Event(), threading.Event()
    readings = iter([0.0])

    def reading() -> float:
        value = next(readings, None)
        if value is not None:
            return value
        reading_started.set()  # the reading after the load: the warm-up is cancelled here
        reading_may_end.wait(5)
        return 2.0

    monkeypatch.setattr(gm, "_cuda_vram_used_gib", reading)
    monkeypatch.setattr(gm, "_free_cuda_cache", lambda: None)
    manager = CudaGpuModelManager(budget_gib=16.0)
    manager.register("stt", _loader([], "stt"), {GpuMode.conversation}, 2.0)

    warm_up = asyncio.create_task(manager.ensure_loaded("stt"))
    await asyncio.to_thread(reading_started.wait, 5)
    warm_up.cancel()
    reading_may_end.set()
    with pytest.raises(asyncio.CancelledError):
        await warm_up
    await asyncio.sleep(0.05)  # the shielded record finishes on its own

    assert manager.ledger()["stt"].loads == 1 and manager.ledger()["stt"].resident
    assert [event for event, _ in box.events] == ["gpu.load"]


async def test_a_release_cut_short_still_finishes_and_is_recorded(
    monkeypatch: pytest.MonkeyPatch, box: _Box
) -> None:
    """The quit that cancels the release after a call (mutation: bookkeeping unshielded)."""
    freeing, may_finish = threading.Event(), threading.Event()

    def free_cache() -> None:
        freeing.set()
        may_finish.wait(5)

    monkeypatch.setattr(gm, "_cuda_vram_used_gib", lambda: 1.0)
    monkeypatch.setattr(gm, "_free_cuda_cache", free_cache)
    manager = CudaGpuModelManager(budget_gib=16.0)
    manager.register("tts", _loader([], "tts"), {GpuMode.conversation}, 3.0)
    await manager.get("tts")

    release = asyncio.create_task(manager.unload("tts"))
    await asyncio.to_thread(freeing.wait, 5)
    release.cancel()
    may_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await release
    await asyncio.sleep(0.05)

    assert not manager.ledger()["tts"].resident
    unloads = [fields for event, fields in box.events if event == "gpu.unload"]
    assert unloads == [{"model": "tts", "reason": "released", "vram_used_mb": 1024}]


async def test_a_model_dropped_for_room_is_recorded_like_any_unload(
    monkeypatch: pytest.MonkeyPatch, box: _Box
) -> None:
    """Evictions used to happen without a trace (mutation: evict silently). The voice
    model loaded by a preview outside a call, in idle mode, makes room for the memory
    model."""
    readings = iter([1.0, 4.0, 15.5, 2.0, 2.5])  # before and after each load, after the drop
    monkeypatch.setattr(gm, "_cuda_vram_used_gib", lambda: next(readings))
    monkeypatch.setattr(gm, "_free_cuda_cache", lambda: None)
    manager = CudaGpuModelManager(budget_gib=16.0)
    manager.register("tts", _loader([], "tts"), {GpuMode.conversation}, 3.0)
    manager.register("embedding", _loader([], "embedding"), {GpuMode.idle}, 1.5)
    await manager.get("tts")

    await manager.get("embedding")  # 15.5 + 1.5 is over 16: the idle voice model goes

    unloads = [fields for event, fields in box.events if event == "gpu.unload"]
    assert unloads == [{"model": "tts", "reason": "budget", "vram_used_mb": 2048}]
    assert not manager.ledger()["tts"].resident
    assert not manager.is_loaded("tts") and manager.is_loaded("embedding")


async def test_everything_is_released_at_shutdown(manager: CudaGpuModelManager, box: _Box) -> None:
    manager.register("stt", _loader([], "stt"), {GpuMode.conversation}, 2.0)
    manager.register("tts", _loader([], "tts"), {GpuMode.conversation}, 3.0)
    manager.register("embedding", _loader([], "embedding"), {GpuMode.idle}, 1.5)
    await manager.set_mode(GpuMode.conversation)

    await manager.unload_all()

    assert not any(manager.is_loaded(model) for model in ("stt", "tts", "embedding"))
    released = [
        (fields["model"], fields["reason"]) for event, fields in box.events if event == "gpu.unload"
    ]
    assert released == [("stt", "shutdown"), ("tts", "shutdown")]


async def test_the_release_at_shutdown_waits_for_a_mode_switch_under_way(
    manager: CudaGpuModelManager,
) -> None:
    """Without the mode lock, a warm-up still loading would put the voice model back on
    the card after the release (mutation: take the load lock alone)."""
    stt_loading, stt_may_finish = threading.Event(), threading.Event()

    def slow_stt() -> str:
        stt_loading.set()
        stt_may_finish.wait(5)
        return "stt-model"

    manager.register("stt", slow_stt, {GpuMode.conversation}, 2.0)
    manager.register("tts", _loader([], "tts"), {GpuMode.conversation}, 3.0)

    warm_up = asyncio.create_task(manager.set_mode(GpuMode.conversation))
    await asyncio.to_thread(stt_loading.wait, 5)
    release = asyncio.create_task(manager.unload_all())
    await asyncio.sleep(0.05)
    stt_may_finish.set()
    await asyncio.gather(warm_up, release)

    assert not manager.is_loaded("stt") and not manager.is_loaded("tts")


def test_sizes_are_gib_inside_and_the_settings_decimal_gb_are_converted() -> None:
    assert round(gm.gib(16.0), 2) == 14.90  # the 16 GB budget is 14.9 GiB, as it always was
    assert gm.gib(1.0737418240) == pytest.approx(1.0)
