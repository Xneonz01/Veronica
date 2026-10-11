"""Timing one text turn for the performance log, by watching what it streams."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.telemetry import current_perf


class TurnTimer:
    """Wraps a turn's `send`: the first token and each tool call are what it times."""

    def __init__(self, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._send = send
        self._started = time.perf_counter()
        self._first_token: float | None = None
        self._tools = 0

    async def send(self, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        if kind == "token" and self._first_token is None:
            self._first_token = time.perf_counter()
        elif kind == "tool":
            self._tools += 1
        await self._send(payload)

    def log(self, *, cited: int) -> None:
        ttft_ms = None
        if self._first_token is not None:
            ttft_ms = round((self._first_token - self._started) * 1000)
        elapsed_ms = (time.perf_counter() - self._started) * 1000.0
        current_perf().log("chat.turn", elapsed_ms, ttft_ms=ttft_ms, tools=self._tools, cited=cited)
