"""What the card has cost, model by model, for the life of the process.

The GPU model manager decides who is on the card now; this keeps what being on it has
cost: how often each model was loaded, reloads included so thrash shows (a model loaded
four times was dropped three times), and how long its last load took and on average.
Whether a model is on the card now is the manager's to say, from the model itself, so a
load or a release cut short can't leave this saying otherwise. `/diagnostics` shows it.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


class ModelCost(BaseModel):
    """One model's history on the card, as `/diagnostics` shows it."""

    loads: int
    last_load_ms: int | None
    mean_load_ms: int | None
    vram_estimate_mb: int  # MiB, as every VRAM figure in her diagnostics
    resident: bool


@dataclass(slots=True)
class _Loads:
    count: int = 0
    last_ms: float | None = None
    total_ms: float = 0.0


class Ledger:
    """Every registered model's loads, fed by the manager."""

    def __init__(self) -> None:
        self._loads: dict[str, _Loads] = {}

    def loaded(self, model: str, load_ms: float) -> None:
        """Count one load of `model` that took `load_ms`."""
        loads = self._loads.setdefault(model, _Loads())
        loads.count += 1
        loads.last_ms = load_ms
        loads.total_ms += load_ms

    def costs(self, estimates_gib: dict[str, float], resident: set[str]) -> dict[str, ModelCost]:
        """Each registered model in registration order, `loads: 0` for one this process
        was told about and never needed."""
        return {
            model: self._cost(model, gib, model in resident) for model, gib in estimates_gib.items()
        }

    def _cost(self, model: str, estimate_gib: float, resident: bool) -> ModelCost:
        loads = self._loads.get(model, _Loads())
        return ModelCost(
            loads=loads.count,
            last_load_ms=None if loads.last_ms is None else round(loads.last_ms),
            mean_load_ms=round(loads.total_ms / loads.count) if loads.count else None,
            vram_estimate_mb=round(estimate_gib * 1024),
            resident=resident,
        )
