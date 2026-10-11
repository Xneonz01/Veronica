"""A synthesis on a worker thread that stays the caller's until the thread is out.

`model.generate` blocks, so the adapter runs it on a thread, under its voice lock. A
barge-in cancels the task waiting on it, and a thread can't be cancelled: the cancel
released the lock with the thread still in the model, so the next sentence could run a
second `generate` beside it, and the abandoned sentence kept the GPU busy until it was
done with a reply nobody would hear.

So the thread is told to stop, and the caller waits until it has. There is one flag for
the process because there is one speech model, and under the adapter's lock one
`generate` at a time. Chatterbox Turbo embeds each speech token it samples with
`t3.speech_emb` (`T3.inference_turbo`, chatterbox-tts 0.1.7), so a forward pre-hook
there reads the flag once a token and raises out of the sampling loop before the
vocoder runs. The loop keeps its state in locals, so leaving it early leaves nothing
behind on the model.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Set while the thread now running has nobody waiting for its result.
ABANDON = threading.Event()


class SynthesisAbandoned(Exception):
    """Raised inside `generate`, at the next speech token, once `ABANDON` is set."""


def install_abandon_check(model: Any) -> None:
    """Stop the model's sampling loop at its next token once `ABANDON` is set.

    A model without the embedding, such as a test double, is left as it is.
    """
    embedding = getattr(getattr(model, "t3", None), "speech_emb", None)
    if embedding is None:
        return

    def check(_module: Any, _inputs: Any) -> None:
        if ABANDON.is_set():
            raise SynthesisAbandoned

    embedding.register_forward_pre_hook(check)


async def until_out[T](work: Callable[[], T]) -> T:
    """Run `work` on a thread; if cancelled, stop it and return only once it is out.

    Called with the adapter's lock held, which is the point: the lock then covers the
    thread and not only the task that was waiting on it.
    """
    ABANDON.clear()
    running = asyncio.ensure_future(asyncio.to_thread(work))
    try:
        return await asyncio.shield(running)
    except asyncio.CancelledError:
        ABANDON.set()
        while not running.done():
            # A second cancel lands here too; the thread is still what to wait for.
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait([running])
        # Retrieved so the loop doesn't report it as never read. The abandoned call ends
        # in `SynthesisAbandoned` by design; anything else is a fault worth a line.
        if not running.cancelled():
            error = running.exception()
            if error is not None and not isinstance(error, SynthesisAbandoned):
                logger.warning("a synthesis nobody was waiting for failed: %r", error)
        raise
    finally:
        # Not when the thread's own task was cancelled, which is the loop shutting
        # down: the thread may still be in the model, and must find the flag set.
        if not running.cancelled():
            ABANDON.clear()
