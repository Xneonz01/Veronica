"""Whether the one language model is serving someone right now.

Chat turns, calls, the end-of-call summary and learning all run on the same model, one
request at a time. Learning can wait and a reply can't, so the learning worker
(`chat/learning_worker.py`) starts only after the model has been quiet for a while. The
model client marks each request it streams (`OllamaLLMClient.stream_chat`). A call
counts separately, through `LiveCalls`: between its turns the model is idle while she
speaks and listens, and the next turn is never more than a pause away.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress


class ModelActivity:
    """A count of the requests someone is waiting on, and when the last one ended.

    Waited on by one coroutine at a time (the learning worker): each wait clears the
    shared event before it reads the count.
    """

    def __init__(self) -> None:
        self._active = 0
        self._ended = time.monotonic()
        self._changed = asyncio.Event()

    @contextmanager
    def busy(self) -> Iterator[None]:
        """Mark the model in use for the length of the block."""
        self._active += 1
        self._changed.set()
        try:
            yield
        finally:
            self._active -= 1
            self._ended = time.monotonic()
            self._changed.set()

    async def claimed(self) -> None:
        """Return once a request is using the model, for work that should give way."""
        while True:
            self._changed.clear()
            if self._active:
                return
            await self._changed.wait()

    async def quiet(self, seconds: float) -> None:
        """Return once nothing has used the model for `seconds`."""
        while True:
            self._changed.clear()  # before reading the state: a change after it wakes us
            left: float | None = None
            if self._active == 0:
                left = seconds - (time.monotonic() - self._ended)
                if left <= 0:
                    return
            with suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), left)
