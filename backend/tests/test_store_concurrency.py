"""Write atomicity on the single shared aiosqlite connection.

sqlite3's legacy transaction mode gives every coroutine on a connection the same
implicit transaction. Without `MemoryStore.transaction()`, an unrelated `commit()`
could commit another coroutine's half-finished multi-statement write, and a
cancelled task could leave the rest of its write pending. Writes do run
concurrently: the session-end summary and the learning extractor can run while the
user deletes memories on the Memory page.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from app.core.event_bus import InProcessEventBus
from app.services.memory.models import MemoryCreate, MemoryKind
from app.services.memory.store import MemoryStore
from tests.fakes import OPEN_STORES, FakeEmbedder, build_fake_memory_stack


async def test_two_stores_on_one_file_keep_their_own_transactions(tmp_path: Path) -> None:
    """Inside one store's transaction, another store's write joined it as if nested: no
    lock of its own, and never committed (mutation: one flag for every store)."""
    first, _service, _retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    second = MemoryStore(tmp_path / "memory.db", FakeEmbedder.DIM)
    await second.init()
    OPEN_STORES.append(second)

    async with first.transaction(), second.transaction() as db:
        await db.execute(
            "INSERT INTO memories (kind, content, created_at, updated_at) "
            "VALUES ('fact', 'x', '2026-09-25T10:00:00', '2026-09-25T10:00:00')"
        )

    async with first.db.execute("SELECT COUNT(*) FROM memories") as cur:
        row = await cur.fetchone()
    assert row is not None and row[0] == 1


async def _vector_ids(store: object) -> set[int]:
    async with store.db.execute("SELECT memory_id FROM memory_vec") as cur:  # type: ignore[attr-defined]
        return {int(row["memory_id"]) for row in await cur.fetchall()}


async def _archived_ids(store: object) -> set[int]:
    async with store.db.execute("SELECT id FROM memories WHERE archived=1") as cur:  # type: ignore[attr-defined]
        return {int(row["id"]) for row in await cur.fetchall()}


async def test_archive_never_half_applies_under_concurrent_writes(tmp_path: Path) -> None:
    # archive_memory is UPDATE + DELETE-vector. A half-applied archive leaves the row
    # flagged archived with its vector alive, and KNN keeps serving a deleted memory.
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    memories = [
        await service.add(MemoryCreate(kind=MemoryKind.fact, content=f"fact number {i}"))
        for i in range(8)
    ]

    await asyncio.gather(
        *(service.delete(m.id) for m in memories[:4]),
        *(
            service.add(MemoryCreate(kind=MemoryKind.fact, content=f"concurrent {i}"))
            for i in range(4)
        ),
    )

    archived = await _archived_ids(store)
    vectors = await _vector_ids(store)
    assert archived == {m.id for m in memories[:4]}
    assert not (archived & vectors), "an archived memory kept its vector"
    await store.close()


async def test_cancelled_archive_leaves_no_half_state(tmp_path: Path) -> None:
    # aiosqlite does not cancel a queued statement, so a task cancelled mid-write could
    # leave its first half pending for whoever commits next.
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    memory = await service.add(MemoryCreate(kind=MemoryKind.fact, content="cancel me"))

    task = asyncio.create_task(service.delete(memory.id))
    await asyncio.sleep(0)  # let it start, then cancel it
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Whatever the outcome, the row and its vector must agree with each other.
    archived = await _archived_ids(store)
    vectors = await _vector_ids(store)
    assert not (archived & vectors), "cancellation left an archived row with a vector"
    await store.close()


async def test_concurrent_profile_writes_keep_one_slot(tmp_path: Path) -> None:
    # upsert_profile is check-then-act across an await: the in-call address hook and the
    # session-end extractor could both see "no row" and both insert, duplicating a
    # Tier-1 slot (which makes the greeting's name flip between calls).
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())

    await asyncio.gather(*(service.set_profile("name", value) for value in ("Rowan", "Rowen")))

    profiles = [m for m in await service.list(kind=MemoryKind.profile) if m.key == "name"]
    assert len(profiles) == 1
    assert (await service.get_profile())["name"] in {"Rowan", "Rowen"}
    await store.close()


async def test_rag_writes_join_the_same_write_lock(tmp_path: Path) -> None:
    # RAG shares the memory store's single connection. With bare commits, a memory
    # transaction rolling back would destroy a document being indexed concurrently,
    # and a RAG commit could commit a half-finished memory archive.
    from tests.fakes import build_fake_rag

    bus = InProcessEventBus()
    store, service, _ = await build_fake_memory_stack(tmp_path, bus)
    rag = await build_fake_rag(store, bus)

    async def failing_memory_write() -> None:
        with contextlib.suppress(RuntimeError):
            async with store.transaction():
                await store.insert_memory(
                    MemoryCreate(kind=MemoryKind.fact, content="doomed by a rollback")
                )
                raise RuntimeError("cancelled mid-write")

    await asyncio.gather(
        rag.index_text("Manual", "A sentence. " * 40),
        failing_memory_write(),
        return_exceptions=True,
    )

    documents = await rag.list_documents()
    assert len(documents) == 1  # the document survived the unrelated rollback
    async with store.db.execute("SELECT COUNT(*) AS n FROM rag_chunks") as cur:
        row = await cur.fetchone()
    assert row is not None and row["n"] > 0  # and kept its chunks (no orphans)
    await store.close()


async def test_approve_keeps_the_candidate_pending_when_embedding_fails(tmp_path: Path) -> None:
    # The embed must happen before the activation commits. Otherwise a GPU failure
    # leaves an approved memory with no vector: never recalled, and gone from the
    # review queue, so the user can't retry it.
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    candidate = await service.add_candidate(
        MemoryCreate(kind=MemoryKind.fact, content="might like jazz")
    )

    class _BoomEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("CUDA out of memory")

    service._embedder = _BoomEmbedder()  # type: ignore[assignment]
    with contextlib.suppress(RuntimeError):
        await service.approve(candidate.id)

    still = await service.get(candidate.id)
    assert still is not None and still.pending  # retryable, not silently half-approved
    assert await _vector_ids(store) == set()
    await store.close()


async def test_failed_embed_does_not_half_apply_a_write(tmp_path: Path) -> None:
    # Embedding is the fallible step (CUDA OOM is realistic on a 16 GB budget), so it
    # runs before the row is written. A failure is then a clean no-op, not an active
    # memory with no vector that shows in the UI but can never be recalled.
    store, service, _ = await build_fake_memory_stack(tmp_path, InProcessEventBus())

    class _BoomEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("CUDA out of memory")

    service._embedder = _BoomEmbedder()  # type: ignore[assignment]
    try:
        await service.add(MemoryCreate(kind=MemoryKind.fact, content="never lands"))
    except RuntimeError:
        pass

    assert await service.list() == []  # no orphan row
    assert await _vector_ids(store) == set()
    await store.close()
