"""MemoryService: typed CRUD + Tier-1 profile + superseding + a sensitivity guard.

Sits on the store + the embedder. Stored content is embedded for retrieval
(except the Tier-1 profile, which is deterministic key/value and never vector-
searched). Content that looks like a secret/credential is refused, not stored.
Every mutation broadcasts `memory.updated` so live surfaces refresh without a reload.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config_memory import MemorySettings
from app.core.event_bus import EventBus
from app.schemas.events import Event
from app.services.memory.address import ADDRESS_KEY
from app.services.memory.models import (
    AddressByName,
    Memory,
    MemoryCreate,
    MemoryKind,
)
from app.services.memory.notify import notify_payload
from app.services.memory.sensitive import SensitiveContentError, is_sensitive
from app.services.memory.store import MemoryStore

# Module-level alias: inside the class body `list` resolves to `MemoryService.list`.
Vector = list[float]


class _Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class MemoryService:
    """Create / read / update / archive memories + the Tier-1 profile."""

    def __init__(
        self, store: MemoryStore, embedder: _Embedder, bus: EventBus, settings: MemorySettings
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._bus = bus
        self._settings = settings

    async def add(self, create: MemoryCreate) -> Memory:
        memory = await self._create(create)
        await self._emit("add", memory)
        await self._enforce_cap()
        return memory

    async def add_if_new(self, create: MemoryCreate) -> Memory | None:
        """Add unless an active memory already covers it; return None when skipped.

        Dedup is normalized-equality on the content, so auto-learning can't stack the
        same observation. Profile entries (Tier-1) reconcile in their own slots.
        """
        if create.kind is not MemoryKind.profile:
            if await self._store.find_active_duplicate(create.content) is not None:
                return None
        return await self.add(create)

    async def get(self, memory_id: int) -> Memory | None:
        return await self._store.get_memory(memory_id)

    async def remembers(self, content: str) -> bool:
        """Whether an active memory already says `content` (the duplicate `add_if_new` skips)."""
        return await self._store.find_active_duplicate(content) is not None

    async def is_waiting(self, create: MemoryCreate) -> bool:
        """Whether the same candidate is already waiting for review."""
        return await self._store.find_waiting_candidate(create.content, create.key) is not None

    async def list(
        self,
        *,
        kind: MemoryKind | None = None,
        pending: bool | None = None,
        include_archived: bool = False,
        limit: int = 200,
    ) -> list[Memory]:
        return await self._store.list_memories(
            kind=kind, pending=pending, include_archived=include_archived, limit=limit
        )

    async def update(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        pinned: bool | None = None,
        origin: str = "agent",
    ) -> Memory | None:
        if content is not None and is_sensitive(content):
            raise SensitiveContentError("Refused to store content that looks like a secret.")
        current = await self._store.get_memory(memory_id)
        if current is None:
            return None
        # Only live, approved, non-profile rows carry a vector. Re-embedding an archived
        # or pending row would make it retrievable: a deleted memory would reach prompts
        # while hidden from the UI, and a candidate would bypass approval.
        revector = (
            content is not None
            and current.kind is not MemoryKind.profile
            and not current.archived
            and not current.pending
        )
        # Embed first: a GPU failure must not leave the row saying one thing while its
        # vector still encodes the old content.
        vector = (
            await self._vector_for(current.model_copy(update={"content": content}))
            if revector
            else None
        )
        async with self._store.transaction():
            memory = await self._store.update_memory(
                memory_id,
                content=content,
                importance=importance,
                confidence=confidence,
                pinned=pinned,
            )
            if memory is not None and vector is not None:
                await self._store.vectors.set_vector(memory_id, vector)
        if memory is None:
            return None
        await self._emit("update", memory, origin=origin)
        return memory

    async def delete(self, memory_id: int, *, origin: str = "agent") -> None:
        memory = await self._store.get_memory(memory_id)  # read first so "removed" has kind
        await self._store.archive_memory(memory_id)
        if memory is not None:
            await self._emit("delete", memory, origin=origin)
        else:
            await self._bus.publish(
                Event(
                    type="memory.updated",
                    source="memory",
                    payload={"op": "delete", "id": memory_id, "action": "removed"},
                )
            )

    async def supersede(self, old_id: int, create: MemoryCreate) -> Memory:
        """Replace an old fact: store the new one, archive the old, link them.

        Both halves are one transaction; a failure between them would otherwise leave
        the old and the new fact both live, i.e. two contradictory memories.
        """
        # Checked here like `add` and `update`: a correction was the one write that
        # could store a secret.
        if is_sensitive(create.content):
            raise SensitiveContentError("Refused to store content that looks like a secret.")
        pending = create.model_copy(update={"supersedes_id": old_id})
        # Fallible GPU work before we mutate; a profile slot is never embedded, and
        # corrects without the memory model (bug 38).
        profile = pending.kind is MemoryKind.profile
        vector = None if profile else await self._vector_for(pending)
        async with self._store.transaction():
            memory = await self._insert_with_vector(pending, vector)
            await self._store.archive_memory(old_id)
        await self._emit("supersede", memory)
        return memory

    async def add_candidate(self, create: MemoryCreate) -> Memory:
        """Store a learning candidate: pending, not embedded, not retrievable until approved."""
        if is_sensitive(create.content):
            raise SensitiveContentError("Refused to store content that looks like a secret.")
        memory = await self._store.insert_memory(create.model_copy(update={"pending": True}))
        await self._emit("candidate", memory)
        return memory

    async def approve(self, memory_id: int) -> Memory | None:
        """Approve a pending candidate: embed it active (or apply a profile correction)."""
        memory = await self._store.get_memory(memory_id)
        if memory is None or not memory.pending:
            return memory
        if memory.kind is MemoryKind.profile and memory.key:
            await self._store.archive_memory(memory_id)  # the candidate becomes the active value
            return await self.set_profile(memory.key, memory.content)
        # Embed first: if the GPU fails, the candidate stays pending (retryable) rather
        # than becoming an "approved" memory with no vector, which could never be recalled.
        vector = await self._vector_for(memory)
        async with self._store.transaction():
            # Turned down while it was embedded: it stays archived, without a vector.
            waiting = await self._store.mark_active(memory_id)
            if waiting and vector is not None:
                await self._store.vectors.set_vector(memory_id, vector)
        activated = await self._store.get_memory(memory_id)
        if waiting and activated is not None:
            await self._emit("approve", activated)
            await self._enforce_cap()
        return activated

    async def reject(self, memory_id: int) -> Memory | None:
        """Turn down a candidate waiting for review (archived); None, and nothing changed,
        when it isn't one. Bug 11: any memory given here was archived."""
        memory = await self._store.get_memory(memory_id)
        if memory is None or not await self._store.archive_candidate(memory_id):
            return None
        await self._emit("delete", memory, origin="ui")
        return memory

    async def restore(self, memory_id: int) -> Memory | None:
        """Bring an archived memory back + re-embed it (Undo of a delete/supersede)."""
        memory = await self._store.get_memory(memory_id)
        if memory is None:
            return None
        # Un-archive and re-vector together, so Undo can't leave a visible but
        # unrecallable memory behind.
        vector = None if memory.kind is MemoryKind.profile else await self._vector_for(memory)
        async with self._store.transaction():
            await self._store.unarchive_memory(memory_id)
            if vector is not None:
                await self._store.vectors.set_vector(memory_id, vector)
        restored = await self._store.get_memory(memory_id)
        if restored is None:  # pragma: no cover - just un-archived
            return None
        await self._emit("add", restored, origin="undo")  # origin "undo" -> no new toast fires
        return restored

    async def undo(self, memory_id: int) -> Memory | None:
        """Reverse a recent write: a supersede rolls back to the original; else archive."""
        memory = await self._store.get_memory(memory_id)
        if memory is None:
            return None
        await self._store.archive_memory(memory_id)
        await self._emit("delete", memory, origin="undo")
        if memory.supersedes_id is not None:
            return await self.restore(memory.supersedes_id)
        return None

    # -- Tier-1 profile (deterministic key/value; never embedded) --

    async def get_profile(self) -> dict[str, str]:
        return await self._store.profile.get_profile()

    async def latest_summary(self) -> str | None:
        """The most recent session summary (for the welcome-back recap)."""
        rows = await self._store.list_memories(kind=MemoryKind.summary, limit=1)
        return rows[0].content if rows else None

    async def set_profile(self, key: str, value: str, *, source: str = "user") -> Memory:
        if is_sensitive(value):
            raise SensitiveContentError(
                "Refused to store a profile value that looks like a secret."
            )
        memory = await self._store.profile.upsert_profile(key, value, source=source)
        await self._emit("profile", memory)
        return memory

    async def set_address_mode(self, mode: AddressByName, *, source: str = "user") -> Memory:
        """Set the deterministic form-of-address slot (Tier-1, one slot, reconciles in place)."""
        return await self.set_profile(ADDRESS_KEY, mode.value, source=source)

    # -- internals --

    async def _create(self, create: MemoryCreate) -> Memory:
        if create.kind is MemoryKind.profile:
            raise ValueError("use set_profile() for Tier-1 profile entries")
        if is_sensitive(create.content):
            raise SensitiveContentError("Refused to store content that looks like a secret.")
        vector = await self._vector_for(create)  # fallible GPU work before we mutate
        async with self._store.transaction():
            return await self._insert_with_vector(create, vector)

    async def _insert_with_vector(self, create: MemoryCreate, vector: Vector | None) -> Memory:
        """Insert a memory and its vector as one unit (call inside a transaction)."""
        memory = await self._store.insert_memory(create)
        if vector is not None:
            await self._store.vectors.set_vector(memory.id, vector)
        return memory

    async def _vector_for(self, item: Memory | MemoryCreate) -> Vector | None:
        """Embed content off the write path, so a GPU failure never half-applies a write."""
        vectors = await self._embedder.embed([item.content])
        return vectors[0] if vectors else None

    async def _enforce_cap(self) -> None:
        """Keep the store bounded: archive the LRU prunable memories over the ceiling.

        Runs after any active memory is created. Tier-1 profile + pinned are never
        prunable, so important knowledge is safe; this only sheds stale conversational
        facts that would otherwise accumulate as noise on the relevance gate.
        """
        over = await self._store.pruning.count_prunable() - self._settings.max_active_memories
        if over > 0:
            await self._store.pruning.prune_oldest(over)

    async def _emit(self, op: str, memory: Memory, *, origin: str = "agent") -> None:
        await self._bus.publish(
            Event(
                type="memory.updated",
                source="memory",
                payload=notify_payload(op, memory, origin=origin),
            )
        )
