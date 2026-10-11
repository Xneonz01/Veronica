"""Controlled learning: extract facts from a conversation, then store or queue them.

Once the model is free, the learning worker (`chat/learning_worker.py`) has an LLM
extractor propose profile updates and candidate facts from what was said, in a chat
conversation or a finished call (`extract`), and files them here (`file`). The
policy follows a live, user-settable mode: in `proactive` (the default)
high-confidence facts are stored active immediately and the rest are queued for
review; in `review_only` nothing is stored active without explicit approval. The
sensitivity filter always wins. There are no silent writes: every learned item emits
`memory.learned` (id, confidence, active or candidate) so a notification and the
awareness log can show it with an Undo. Corrections to the user's identity go through
the Tier-1 profile, which updates in place. The request, the parser and the filing
rule are pure functions in `extraction`.

Filing is safe to repeat: what she already holds, a candidate already waiting and a
profile value she already has are each skipped, so reading the same turns twice (a
start after a quit mid-filing) announces nothing twice. So is a fact that only repeats,
in other words, what the person asked her to keep that day (`copy_of`, bug 39).

A conversation is read in parts (`chat/learning_worker.py`). A part after the first is
sent with the last turns read before it, so that "she's vegetarian" has someone to be;
what the model restates of them, or of what it learned that day, isn't filed (`sift`,
bug 40).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import timedelta
from typing import Any, Protocol

from app.adapters.llm.types import LlmChunk
from app.core.config_memory import MemorySettings
from app.core.event_bus import EventBus
from app.schemas.events import Event
from app.services.memory.address import ADDRESS_KEY, classify_directive, coerce_address
from app.services.memory.extraction import (
    Filing,
    extraction_messages,
    filing_for,
    kept_profile,
    parse_extraction,
    session_transcript,
    sift,
)
from app.services.memory.models import (
    AddressByName,
    Extraction,
    LearningMode,
    Memory,
    MemoryCreate,
    MemoryKind,
    _now,
)
from app.services.memory.notify import learned_payload
from app.services.memory.sensitive import SensitiveContentError
from app.services.memory.service import MemoryService
from app.services.memory.store import MemoryStore
from app.telemetry import current_blackbox, current_perf

logger = logging.getLogger(__name__)

History = Sequence[Mapping[str, Any]]
# What a learned fact is held against: what she was asked to keep the day before it is
# filed, which covers the conversation just read. A narrow list keeps a wrong match rare;
# a copy of something older still gets through.
_SAME_DAY = timedelta(days=1)


class LlmStream(Protocol):
    """The one method of the model client that learning uses."""

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        *,
        model: str | None = None,
        keep_alive: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LlmChunk]: ...


class LearningController:
    """Holds the live learning mode and applies the extraction policy."""

    def __init__(
        self, service: MemoryService, bus: EventBus, settings: MemorySettings, store: MemoryStore
    ) -> None:
        self._service = service
        self._bus = bus
        self._s = settings
        self._store = store
        self._mode = LearningMode(settings.learning_mode)

    @property
    def mode(self) -> LearningMode:
        return self._mode

    async def set_mode(self, mode: LearningMode) -> None:
        """Change proactivity live (a user setting); broadcast so surfaces update."""
        self._mode = mode
        await self._bus.publish(
            Event(type="memory.learning_mode", source="learning", payload={"mode": mode.value})
        )

    async def extract(
        self, history: History, llm: LlmStream, model: str, earlier: History = ()
    ) -> Extraction | None:
        """What the model proposes from `history`, with `earlier` (the turns read before
        it) as context: None when its answer couldn't be read, an empty extraction when
        the user said nothing. Whatever stopped the request is raised as it was
        (`OllamaUnreachableError` when the model is away), so a failure isn't taken for
        an answer too long to read: that one reads less next time
        (`chat/learning_worker.py`), and a model being pulled says nothing about the
        turns."""
        if not any(message.get("role") == "user" for message in history):
            return Extraction()  # she only spoke: nothing the user said to learn from
        transcript = session_transcript(history)
        before = session_transcript(earlier)
        started = time.perf_counter()
        extraction: Extraction | None = None
        try:
            extraction = await self._ask(extraction_messages(transcript, before), llm, model)
        finally:
            current_perf().log(
                "memory.learn",
                (time.perf_counter() - started) * 1000.0,
                transcript_chars=len(transcript),
                earlier_chars=len(before),
                ok=extraction is not None,
                malformed=extraction.malformed if extraction is not None else 0,
            )
        return extraction

    async def file(self, extraction: Extraction, history: History, earlier: History = ()) -> None:
        """Store or queue what was extracted from `history`, read after `earlier` (the
        turns sent with it). One item failing doesn't stop the rest."""
        coalesce_id = uuid.uuid4().hex  # one id per extraction -> the UI collapses the burst
        # Profile keys are capped like facts. Tier-1 rows are pinned and exempt from the
        # LRU ceiling, and the whole profile goes into every prompt, so an extractor
        # that invents keys would grow a latency-critical block with no way to shed it.
        profile = kept_profile(extraction.profile, history)
        for key, value in list(profile.items())[: self._s.learning_max_facts]:
            try:
                await self._ingest_profile(key, value, coalesce_id)
            except Exception as exc:
                logger.warning("could not file a learned profile detail (%s)", type(exc).__name__)
        since = _now() - _SAME_DAY
        recent = self._store.recent
        kept = await recent.asked_to_keep(since) if extraction.facts else []
        learned = await recent.learned(since) if extraction.facts and earlier else []
        sifted = sift(extraction.facts, history, kept, earlier=earlier, learned=learned)
        for event, count in (
            ("learning.example_dropped", sifted.examples),
            ("learning.copy_dropped", sifted.copies),
            ("learning.earlier_dropped", sifted.earlier),
        ):
            if count:
                current_blackbox().record("info", event, count=count)
        for fact in sifted.facts[: self._s.learning_max_facts]:
            try:
                await self._ingest_fact(fact.content, fact.confidence, fact.kind, coalesce_id)
            except Exception as exc:
                logger.warning("could not file a learned fact (%s)", type(exc).__name__)

    async def _ask(
        self, messages: list[dict[str, Any]], llm: LlmStream, model: str
    ) -> Extraction | None:
        # The model stays loaded: it is the one chat and calls use, so releasing it
        # here only made the next turn wait for it to load again.
        parts = [
            chunk.content
            async for chunk in llm.stream_chat(
                messages,
                tools=None,
                model=model,
                temperature=self._s.learning_temperature,
            )
            if chunk.content
        ]
        try:
            return parse_extraction("".join(parts))
        except ValueError:
            # Not the JSON asked for: an answer that couldn't be read, which the type
            # alone says (a message could quote it, and the user).
            logger.warning("learning extraction couldn't be read")
            return None

    async def _ingest_profile(self, key: str, value: str, coalesce_id: str) -> None:
        if key == ADDRESS_KEY:  # a deterministic slot, validated through the enum
            await self._apply_address(coerce_address(value), coalesce_id)
            return
        if self._mode is LearningMode.review_only:
            await self._queue(
                MemoryCreate(
                    kind=MemoryKind.profile,
                    key=key,
                    content=value,
                    source="inferred",
                    confidence=0.9,
                ),
                coalesce_id,
            )
            return
        if (await self._service.get_profile()).get(key) == value:
            return  # already hers: nothing to announce
        try:
            # inferred -> the UI renders it dimmer/dashed (vs explicit, solid)
            memory = await self._service.set_profile(key, value, source="inferred")
        except SensitiveContentError:
            return
        await self._announce(memory, active=True, coalesce_id=coalesce_id)

    async def _ingest_fact(
        self, content: str, confidence: float, kind: MemoryKind, coalesce_id: str
    ) -> None:
        filing = filing_for(
            content, confidence, kind, self._mode, self._s.learning_confidence_threshold
        )
        if filing is Filing.address:
            # "Don't call me by name" is a Tier-1 form-of-address slot, not a stacked
            # preference — route it so it reconciles with the name in one place.
            directive = classify_directive(content)
            if directive is not None:
                await self._apply_address(directive, coalesce_id)
            return
        if filing is Filing.drop:
            # Only durable facts/preferences are learned. Episodic "what happened this
            # call" notes are transient meta-state (the session summary already covers
            # the call), so they are dropped, never auto-stored as durable memory.
            return
        create = MemoryCreate(kind=kind, content=content, source="inferred", confidence=confidence)
        if filing is Filing.queue:
            await self._queue(create, coalesce_id)
            return
        try:
            memory = await self._service.add_if_new(create)  # active + embedded, deduped
        except SensitiveContentError:
            return
        except Exception as exc:
            # Storing it active needs the memory model; one that failed to load would lose
            # the fact. Waiting for review needs no model, so it goes there instead.
            logger.warning("a learned fact went to review instead (%s)", type(exc).__name__)
            await self._queue(create, coalesce_id)
            return
        if memory is None:
            return  # a near-duplicate of an existing active memory — nothing new to add
        await self._announce(memory, active=True, coalesce_id=coalesce_id)

    async def _queue(self, create: MemoryCreate, coalesce_id: str) -> None:
        if await self._service.is_waiting(create):
            return  # the same candidate is already waiting for review
        try:
            memory = await self._service.add_candidate(create)
        except SensitiveContentError:
            return
        await self._announce(memory, active=False, coalesce_id=coalesce_id)

    async def _apply_address(self, mode: AddressByName, coalesce_id: str) -> None:
        """Apply an inferred form-of-address correction to the Tier-1 slot.

        Respects the live mode like any other learned write: proactive applies it
        active (the value is a controlled enum, never sensitive); review_only queues
        a profile candidate that `approve` turns into the slot value. Idempotent —
        if the slot already holds this value (e.g. the in-call hook applied it during
        the call), it does nothing, so the session-end pass can't re-notify.
        """
        if (await self._service.get_profile()).get(ADDRESS_KEY) == mode.value:
            return
        if self._mode is LearningMode.review_only:
            await self._queue(
                MemoryCreate(
                    kind=MemoryKind.profile,
                    key=ADDRESS_KEY,
                    content=mode.value,
                    source="inferred",
                    confidence=0.9,
                ),
                coalesce_id,
            )
            return
        memory = await self._service.set_address_mode(mode, source="inferred")
        await self._announce(memory, active=True, coalesce_id=coalesce_id)

    async def _announce(self, memory: Memory, *, active: bool, coalesce_id: str) -> None:
        # No silent writes: every learned item surfaces (notification + awareness log).
        payload = learned_payload(memory, active=active, coalesce_id=coalesce_id)
        await self._bus.publish(Event(type="memory.learned", source="learning", payload=payload))
