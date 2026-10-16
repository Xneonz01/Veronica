"""Background work kept track of: a server's restarts and refreshes, the runtime's starts.

A task is kept until it ends, so it isn't collected half way, can be cancelled with the
rest, and a failure in it is logged by its kind rather than lost."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class Tasks:
    def __init__(self) -> None:
        self._running: set[asyncio.Task[None]] = set()

    def spawn(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(work)
        self._running.add(task)
        task.add_done_callback(self._ended)
        return task

    def cancel(self) -> list[asyncio.Task[None]]:
        """Cancel all but the task asking (a restart runs inside one); those cancelled."""
        cancelled = [task for task in self._running if task is not asyncio.current_task()]
        for task in cancelled:
            task.cancel()
        return cancelled

    def _ended(self, task: asyncio.Task[None]) -> None:
        self._running.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("a tool server's task failed: %s", type(task.exception()).__name__)
