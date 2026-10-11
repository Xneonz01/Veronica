"""In-process async pub/sub event bus.

Services publish typed events. Each WebSocket connection subscribes a forwarder for
the types its client needs (`ClientForwarder` for a `/ws/voice` call,
`AppEventForwarder` for `/ws/events`). `publish` awaits every subscriber, so a
subscriber must not block on slow I/O.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.schemas.events import Event

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]


class EventBus(Protocol):
    """Publish/subscribe contract that decouples producers from consumers."""

    async def publish(self, event: Event) -> None: ...
    def subscribe(self, event_type: str, handler: Handler) -> Callable[[], None]: ...


class InProcessEventBus:
    """Async in-process pub/sub keyed by event type."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, event_type: str, handler: Handler) -> Callable[[], None]:
        """Register handler for event_type; returns an idempotent unsubscribe."""
        self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event_type)
            if handlers is not None and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: Event) -> None:
        """Deliver event concurrently to every subscriber; isolate handler failures.

        One handler raising must never cancel delivery to the others, so failures
        are gathered and logged rather than propagated back to the publisher.
        """
        handlers = list(self._handlers.get(event.type, ()))
        if not handlers:
            return
        results = await asyncio.gather(
            *(handler(event) for handler in handlers), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("event handler failed for %s: %r", event.type, result)
