"""Which calls are live, and letting the speech models go once none is.

Whisper and Chatterbox are loaded for a call (`GpuMode.conversation`) and nothing
unloaded them afterwards, so their VRAM stayed taken until Veronica quit. Now, when the
last call ends, the manager goes back to `GpuMode.idle`, which keeps the memory model
chat needs and releases the two speech models: 4.2 GB, measured. It waits
`release_after_s` first. A call started again inside that window warmed up in 0.39 s;
one started after the release took 7.3 s, loading them again. A voice preview outside a
call loads the voice model too, and lets it go the same way.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Protocol

from app.core.gpu_manager import GpuMode, GpuModelManager

logger = logging.getLogger(__name__)


class _Call(Protocol):
    @property
    def call_active(self) -> bool: ...


class LiveCalls:
    """The calls on this process, and the release of the speech models after the last."""

    def __init__(self, manager: GpuModelManager) -> None:
        self._manager = manager
        self._calls: weakref.WeakSet[_Call] = weakref.WeakSet()
        self._timer: asyncio.TimerHandle | None = None
        self._releasing: asyncio.Task[None] | None = None

    def count(self) -> int:
        """Calls live now.

        Read off each call rather than counted up and down: a call ends by `stop` or by
        a failed warm-up, and a count missed on either would keep the models loaded for
        good. Held weakly, so a call that was never stopped stops counting once it is
        gone.
        """
        return sum(1 for call in list(self._calls) if call.call_active)

    def started(self, call: _Call) -> None:
        """A call is starting. It keeps the models, so a pending release is called off."""
        self._calls.add(call)
        self._cancel_timer()

    def ended(self, release_after_s: float) -> None:
        """A call has ended; if it was the last, release the models `release_after_s` on."""
        self.release_later(release_after_s)

    def release_later(self, release_after_s: float) -> None:
        """Release the speech models `release_after_s` from now unless a call holds them:
        after the last call, and after a voice preview outside one, which loaded the
        voice model and kept it until the next call ended (bug 13)."""
        if self.count() > 0:
            return
        self._cancel_timer()
        self._timer = asyncio.get_running_loop().call_later(release_after_s, self._release)

    def close(self) -> None:
        """At shutdown: start no release of its own; the lifespan releases every model
        (`CudaGpuModelManager.unload_all`)."""
        self._cancel_timer()
        if self._releasing is not None:
            self._releasing.cancel()

    def _release(self) -> None:
        # No call can be live here: every start calls the timer off first.
        self._timer = None
        self._releasing = asyncio.create_task(self._manager.set_mode(GpuMode.idle, load=False))
        self._releasing.add_done_callback(_report_failure)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def _report_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("releasing the speech models failed: %r", task.exception())
