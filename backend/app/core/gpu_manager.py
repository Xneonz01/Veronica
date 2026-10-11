"""GPU model manager: the single owner of VRAM-resident models.

Adapters never load models directly: a loader is registered here and adapters
request the instance via ``get``. The manager lazy-loads, tracks the active mode,
and unloads models the current mode does not need, within the VRAM budget.

Sizes are GiB inside (the settings give decimal GB: `gib` converts), and the black box
gets MiB, as every VRAM figure there is. What each model has cost the card is kept in a
ledger (`core/gpu_ledger.py`). This module stays torch-free at import time (every
runtime and many tests import it, and importing torch takes 1.4 s); torch is imported
lazily inside the functions that touch the device, and every call into it runs off the
event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.core.gpu_ledger import Ledger, ModelCost
from app.telemetry import current_blackbox, current_perf

logger = logging.getLogger(__name__)

_GIB = 1024**3


class GpuMode(StrEnum):
    """Operating modes that decide which models stay resident in VRAM."""

    idle = "idle"
    conversation = "conversation"


class GpuModelManager(Protocol):
    """Loads/unloads models and switches modes within the VRAM budget."""

    async def get(self, model_id: str) -> Any: ...
    async def ensure_loaded(self, model_id: str) -> None: ...
    async def unload(self, model_id: str) -> None: ...
    async def set_mode(self, mode: GpuMode, *, load: bool = True) -> None: ...
    def is_loaded(self, model_id: str) -> bool: ...
    def vram_used_gib(self) -> float: ...


ModelLoader = Callable[[], Any]


def gib(gigabytes: float) -> float:
    """Decimal gigabytes, as the settings and the estimates are written, in GiB."""
    return gigabytes * 1e9 / _GIB


@dataclass(slots=True)
class _ModelSpec:
    loader: ModelLoader
    modes: frozenset[GpuMode]
    vram_estimate_gib: float
    instance: Any = None


def _cuda_vram_used_gib() -> float:
    """VRAM in use on the default CUDA device (all processes), in GiB; 0 if no CUDA."""
    try:
        import torch
    except ModuleNotFoundError:
        return 0.0
    if not torch.cuda.is_available():
        return 0.0
    free, total = torch.cuda.mem_get_info()
    return float(total - free) / _GIB


def _free_cuda_cache() -> None:
    """Release cached CUDA blocks back to the device after an unload."""
    try:
        import torch
    except ModuleNotFoundError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


class CudaGpuModelManager:
    """Lazily loads and unloads GPU models within a VRAM budget.

    Externally-managed engines (e.g. Ollama) are not registered here — their
    VRAM shows up in ``vram_used_gib`` but the manager does not own them.
    """

    def __init__(self, budget_gib: float) -> None:
        self._budget_gib = budget_gib
        self._specs: dict[str, _ModelSpec] = {}
        self._mode = GpuMode.idle
        self._lock = asyncio.Lock()
        # Mode switches run one at a time: the release after a call (to idle) and the
        # next call's warm-up (to conversation) would otherwise interleave their loads
        # and unloads, and the call could be left without a model it had just loaded.
        self._mode_lock = asyncio.Lock()
        self._ledger = Ledger()

    def register(
        self,
        model_id: str,
        loader: ModelLoader,
        modes: Iterable[GpuMode],
        vram_estimate_gib: float,
    ) -> None:
        """Register how to load a model and which modes keep it resident."""
        self._specs[model_id] = _ModelSpec(loader, frozenset(modes), vram_estimate_gib)

    def unregister(self, model_id: str) -> None:
        """Forget a model that isn't loaded, so no mode loads it: the memory model of a
        memory file that couldn't be opened, which nothing will use. Only before anything
        can have asked for it (the start): a load waiting on the lock would be left
        holding a model nobody can get."""
        spec = self._specs.get(model_id)
        if spec is not None and spec.instance is None:
            del self._specs[model_id]

    async def get(self, model_id: str) -> Any:
        """Ensure the model is loaded and return the live instance."""
        await self.ensure_loaded(model_id)
        return self._require(model_id).instance

    async def ensure_loaded(self, model_id: str) -> None:
        spec = self._require(model_id)
        if spec.instance is not None:
            return
        async with self._lock:
            if spec.instance is not None:
                return
            # Off the loop: the first reading imports torch and makes the CUDA context,
            # which held every request as long (a start's warm-up, a call's).
            used = await asyncio.to_thread(self.vram_used_gib)
            await self._enforce_budget(spec.vram_estimate_gib, used)
            logger.info("Loading model %s (~%.1f GiB)", model_id, spec.vram_estimate_gib)
            started = time.perf_counter()
            spec.instance = await asyncio.to_thread(spec.loader)
            load_ms = (time.perf_counter() - started) * 1000.0
            # Counted before anything else awaits: a warm-up cancelled from here on still
            # leaves the model loaded, and the ledger and the perf log must say so.
            self._ledger.loaded(model_id, load_ms)
            current_perf().log("gpu.load", load_ms, model=model_id)
            await asyncio.shield(self._record_load(model_id, load_ms))

    async def unload(self, model_id: str) -> None:
        spec = self._require(model_id)
        if spec.instance is None:
            return
        async with self._lock:
            if spec.instance is None:
                return
            spec.instance = None
            await asyncio.shield(self._released([model_id], reason="released"))

    async def unload_all(self) -> None:
        """Release every loaded model: at shutdown, so none is left for the exit to
        reclaim unseen, and the reading `app.stop` takes shows what is left on the card.
        Under the mode lock, so no mode switch loads one back meanwhile. A load still
        running in its thread (a call hung up during a cold warm-up) finishes there, and
        goes with the process."""
        async with self._mode_lock, self._lock:
            loaded = [
                model_id for model_id, spec in self._specs.items() if spec.instance is not None
            ]
            for model_id in loaded:
                self._specs[model_id].instance = None
            if loaded:
                await asyncio.shield(self._released(loaded, reason="shutdown"))

    async def set_mode(self, mode: GpuMode, *, load: bool = True) -> None:
        """Unload the models the mode doesn't need, and load the ones it does.

        `load=False` only lets go: the release after a call must free the speech models,
        not load the memory model because idle mode lists it (a call that failed before
        loading it would otherwise have it loaded a minute after it ended).
        """
        async with self._mode_lock:
            self._mode = mode
            logger.info("GPU mode -> %s", mode.value)
            # A copy: the loads below await, and the table must not change under them.
            for model_id, spec in list(self._specs.items()):
                if mode in spec.modes:
                    if load:
                        await self.ensure_loaded(model_id)
                elif spec.instance is not None:
                    await self.unload(model_id)

    def is_loaded(self, model_id: str) -> bool:
        """Whether the model is in memory now; asking never loads it."""
        return self._require(model_id).instance is not None

    def to_load(self, mode: GpuMode) -> set[str]:
        """The models `mode` keeps resident that aren't in memory now: what switching to it
        would still load. One unregistered (the memory model of a memory file that
        couldn't be opened) is never among them."""
        return {
            model_id
            for model_id, spec in self._specs.items()
            if mode in spec.modes and spec.instance is None
        }

    def vram_used_gib(self) -> float:
        return _cuda_vram_used_gib()

    def ledger(self) -> dict[str, ModelCost]:
        """What each registered model has cost the card so far, for `/diagnostics`; whether
        it is on the card now is read from the model itself."""
        return self._ledger.costs(
            {model_id: spec.vram_estimate_gib for model_id, spec in self._specs.items()},
            {model_id for model_id, spec in self._specs.items() if spec.instance is not None},
        )

    def _require(self, model_id: str) -> _ModelSpec:
        spec = self._specs.get(model_id)
        if spec is None:
            raise KeyError(f"model not registered: {model_id}")
        return spec

    async def _enforce_budget(self, need_gib: float, used_gib: float) -> None:
        """Free the models the current mode doesn't need if the budget is tight, each
        recorded as any unload is."""
        if used_gib + need_gib <= self._budget_gib:
            return
        idle = [
            model_id
            for model_id, spec in self._specs.items()
            if spec.instance is not None and self._mode not in spec.modes
        ]
        for model_id in idle:
            logger.info("Budget pressure: unloading idle %s", model_id)
            self._specs[model_id].instance = None
        if idle:
            used_gib = await asyncio.shield(self._released(idle, reason="budget"))
        if used_gib + need_gib > self._budget_gib:
            logger.warning(
                "VRAM budget tight: used=%.1f + need=%.1f > budget=%.1f GiB",
                used_gib,
                need_gib,
                self._budget_gib,
            )

    async def _record_load(self, model_id: str, load_ms: float) -> None:
        """The black box told of a load, with the VRAM in use after it. Shielded by its
        caller, so a warm-up cancelled meanwhile doesn't lose the record."""
        used_mib = round(await asyncio.to_thread(self.vram_used_gib) * 1024)
        logger.info("Loaded %s | vram_used=%d MiB", model_id, used_mib)
        current_blackbox().record(
            "info", "gpu.load", model=model_id, load_ms=round(load_ms), vram_used_mb=used_mib
        )

    async def _released(self, model_ids: list[str], *, reason: str) -> float:
        """After models are dropped: the cache handed back to the device once, and the
        black box told of each. Shielded by its callers, so a release cut short (the
        quit that cancels the release after a call) still finishes. The VRAM in use
        after, in GiB."""
        await asyncio.to_thread(_free_cuda_cache)
        used_gib = await asyncio.to_thread(self.vram_used_gib)
        used_mib = round(used_gib * 1024)
        for model_id in model_ids:
            logger.info("Unloaded %s (%s) | vram_used=%d MiB", model_id, reason, used_mib)
            current_blackbox().record(
                "info", "gpu.unload", model=model_id, reason=reason, vram_used_mb=used_mib
            )
        return used_gib
