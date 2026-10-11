"""Settings for the black box and the performance log (the `telemetry:` config section)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.paths import Place


class TelemetrySettings(BaseModel):
    """Where local telemetry is written and how much of it is kept."""

    enabled: bool = True
    dir: Place = "data/telemetry"  # in `Settings`, in the data folder unless set
    runs_kept: int = Field(default=20, gt=0)
    blackbox_queue_size: int = Field(default=1000, gt=0)
    blackbox_max_run_bytes: int = Field(default=5_000_000, gt=0)
    redaction_clip: int = Field(default=2000, gt=0)
    perf_max_bytes: int = Field(default=10_000_000, gt=0)
    perf_files_kept: int = Field(default=5, gt=0)
    # Log records at or above this level are copied into the black box.
    capture_log_level: Literal["WARNING", "ERROR"] = "WARNING"
