"""Identity of one backend process run, stamped on every telemetry line."""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RunContext:
    """Who wrote a telemetry line: which run of which build.

    `run_id` starts with a local timestamp so run folders sort chronologically by
    name, which is what the black box's retention relies on.
    """

    run_id: str
    app_version: str
    channel: str
    pid: int
    started_at: str

    @classmethod
    def create(cls, *, app_version: str, channel: str) -> RunContext:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return cls(
            run_id=f"{stamp}_{secrets.token_hex(3)}",
            app_version=app_version,
            channel=channel,
            pid=os.getpid(),
            started_at=datetime.now(UTC).isoformat(),
        )
