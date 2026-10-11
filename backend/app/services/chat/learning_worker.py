"""The worker that learns from what was said, one thing at a time, when the model is free.

It takes a finished call (`memory/call_queue.py`) before a chat conversation
(`learning_queue.py`), each the one that has waited longest, and waits until no call is
live and nothing has used the model for `memory.learning_quiet_s`. Then it reads the
turns learning hasn't seen (`learning_sources.py`), with the last few it has as context,
and hands them to the extractor (`LearningController`). Its own requests don't count as use
(`OllamaLLMClient.background`), and when a reply or a call starts while it extracts, it
stops and leaves what it read due: a reply never waits behind learning.

An answer that can't be read keeps it due, and the next attempt reads half as much: a
prompt past the model's window loses its front, the instruction, and a long paste of
code or logs packs more tokens into its characters than prose does. A part still
unreadable at the smallest size is passed over (`learning.skipped`), so one paste can't
stop the rest from being learned. A model that can't be reached costs no attempt; it is
tried again a minute later.

Restart-safe by construction: an extraction cut off at shutdown never reached `done`, so
what it read is still due at the next start, and filing the same turns twice files
nothing twice (`LearningController`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Final

from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaUnreachableError,
)
from app.core.model_activity import ModelActivity
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.learning_sources import CallSource, ChatSource, Part, PendingT, Source
from app.services.chat.store import ChatStore
from app.services.memory.call_queue import CallQueue
from app.services.memory.learning import LearningController, LlmStream
from app.services.memory.learning_retry import UNREADABLE
from app.services.memory.models import Extraction
from app.telemetry import current_blackbox

logger = logging.getLogger(__name__)

_IDLE_POLL_S = 60.0  # nothing due, or the model away: look again after this
_CALL_POLL_S = 5.0  # a call is live: look again after this
_WATCH_S = 0.5  # while extracting: how often to look for a call starting
# The extraction goes out with `llm.num_ctx` tokens, and a prompt longer than that loses
# its front (the instruction) without an error. 1,024 tokens are kept for the
# instruction (about 300) and the answer. English runs about four characters to a
# token; three leaves room for text that packs fewer.
_RESERVED_TOKENS = 1024
_CHARS_PER_TOKEN = 3
_SMALLEST = 2000  # characters: the least an attempt reads before a part is passed over
_GAVE_WAY: Final = object()


def transcript_budget(num_ctx: int) -> int:
    """How many characters of conversation one extraction reads."""
    return (num_ctx - _RESERVED_TOKENS) * _CHARS_PER_TOKEN


class LearningWorker:
    """Drains the queues: one call or conversation, one extraction at a time."""

    def __init__(
        self,
        queue: LearningQueue,
        chat: ChatStore,
        learning: LearningController,
        llm: LlmStream,
        activity: ModelActivity,
        *,
        calls_live: Callable[[], int],
        model: str,
        quiet_s: float,
        max_chars: int,
        calls: CallQueue | None = None,
    ) -> None:
        # A finished call first: its words are kept only until they are read.
        chats: Source[Any] = ChatSource(queue, chat)
        self._sources: tuple[Source[Any], ...] = (
            (CallSource(calls), chats) if calls is not None else (chats,)
        )
        self._learning = learning
        self._llm = llm  # uncounted: see the module docstring
        self._activity = activity
        self._calls_live = calls_live
        self._model = model
        self._quiet_s = quiet_s
        self._max_chars = max_chars

    async def run(self) -> None:
        """Drain for the life of the process; only cancellation at shutdown ends it."""
        while True:
            try:
                await self.wait_until_free()
                worked = await self.step()
            except Exception:  # the store refused, most likely: the worker outlives it
                logger.exception("learning hit an error; it tries again in a minute")
                worked = False
            if not worked:
                await asyncio.sleep(_IDLE_POLL_S)

    async def wait_until_free(self) -> None:
        """Until no call is live and nothing has used the model for `quiet_s`."""
        while True:
            await self._activity.quiet(self._quiet_s)
            if self._calls_live() == 0:
                return
            await asyncio.sleep(_CALL_POLL_S)

    async def step(self) -> bool:
        """Learn from what has waited longest, a finished call before a conversation.
        False when nothing was due, or the model couldn't be reached: then there is
        nothing to do for now."""
        for source in self._sources:
            due = await source.next_due()
            if due is not None:
                return await self._learn(source, due)
        return False

    async def _learn(self, source: Source[PendingT], due: PendingT) -> bool:
        # Each answer in a row that couldn't be read halves what the next attempt reads,
        # down to the least. A request that failed says nothing about the length: read
        # in full again. (A failure from before reasons were kept has none: as before.)
        least = min(_SMALLEST, self._max_chars)
        shrink = due.attempts if due.last_error in (None, UNREADABLE) else 0
        budget = max(self._max_chars >> shrink, least)
        part = await source.part(due, budget)
        if not part.said:
            await source.done(due, through=part.through)
            return True
        try:
            extraction = await self._extract(part)
        except (OllamaUnreachableError, OllamaModelMissingError, OllamaModelFailedError):
            # Nothing about the turns: a model away, being pulled, or that couldn't run.
            logger.debug("learning waits: the model can't be reached, installed or run")
            return False
        except Exception as exc:  # the model failed partway: an error from its side
            await self._failed(source, due, type(exc).__name__)
            return True
        if extraction is _GAVE_WAY:
            return True  # still due, and taken again once the model is free
        if not isinstance(extraction, Extraction):
            if budget > least:
                await self._failed(source, due, UNREADABLE)
                return True
            await source.done(due, through=part.through)
            logger.warning("learning passed over a part of what was said it couldn't read")
            current_blackbox().record("warn", "learning.skipped", turns=len(part.said))
            return True
        if not await source.kept(due):
            return True
        await self._learning.file(extraction, part.said, part.earlier)
        await source.done(due, through=part.through)
        return True

    async def _extract(self, part: Part) -> Extraction | None | object:
        """The extraction, or `_GAVE_WAY` when a reply or a call needed the model first."""
        extracting = asyncio.ensure_future(
            self._learning.extract(part.said, self._llm, self._model, part.earlier)
        )
        needed = asyncio.ensure_future(self._needed())
        try:
            await asyncio.wait({extracting, needed}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Also on cancellation at shutdown: nothing is left running behind it.
            for task in (extracting, needed):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
        if not extracting.cancelled():
            return extracting.result()  # re-raises what the extraction raised
        return _GAVE_WAY

    async def _needed(self) -> None:
        """Return once a reply or a call wants the model."""
        while self._calls_live() == 0:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._activity.claimed(), _WATCH_S)
                return

    async def _failed(self, source: Source[PendingT], due: PendingT, error: str) -> None:
        wait = await source.failed(due, error)
        # The error's type or "unreadable" only: a message can quote the reply.
        logger.warning(
            "learning failed (%s, attempt %d); trying again in %d s",
            error,
            due.attempts + 1,
            wait,
        )
        current_blackbox().record(
            "warn", "learning.failed", error=error, attempts=due.attempts + 1, retry_s=wait
        )
