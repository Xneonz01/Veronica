"""MemoryRetriever: the relevance gate and the always-included Tier-1 profile."""

from __future__ import annotations

from pathlib import Path

from app.core.event_bus import InProcessEventBus
from app.services.memory.models import MemoryCreate, MemoryKind
from tests.fakes import build_fake_memory_stack


async def test_gate_drops_offtopic_recent_important(tmp_path: Path) -> None:
    """An off-topic memory must not get into a turn on recency and importance alone."""
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.add(MemoryCreate(kind=MemoryKind.fact, content="feeding a sourdough starter"))
    # Off-topic, but fresh and very important.
    await service.add(
        MemoryCreate(kind=MemoryKind.episodic, content="mom and family", importance=0.95)
    )

    ctx = await retriever.retrieve("how do I feed the sourdough starter?")
    contents = [item.memory.content for item in ctx.items]
    assert any("sourdough" in c for c in contents)  # on-topic retrieved
    assert all("mom" not in c for c in contents)  # off-topic gated out despite recency+importance
    assert "memory" in ctx.tiers_used
    await store.close()


async def test_gate_ignores_stopword_only_overlap(tmp_path: Path) -> None:
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    # Shares only the stopword "the" with the query -> no real signal, must be gated.
    await service.add(MemoryCreate(kind=MemoryKind.fact, content="the weather is warm"))
    ctx = await retriever.retrieve("feed the sourdough")
    assert all("weather" not in item.memory.content for item in ctx.items)
    await store.close()


async def test_profile_always_present(tmp_path: Path) -> None:
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.set_profile("name", "Rowan")
    ctx = await retriever.retrieve("how do I feed the sourdough?")  # unrelated to the name
    assert ctx.profile == {"name": "Rowan"}  # Tier-1 always included
    await store.close()


async def test_empty_query_returns_profile_only(tmp_path: Path) -> None:
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.set_profile("name", "Rowan")
    await service.add(MemoryCreate(kind=MemoryKind.fact, content="Sourdough baking"))
    ctx = await retriever.retrieve("   ")  # welcome-back: no query signal
    assert ctx.profile == {"name": "Rowan"}
    assert ctx.items == []
    assert ctx.tiers_used == ["profile"]
    await store.close()


async def test_pinned_memory_bypasses_gate(tmp_path: Path) -> None:
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.add(
        MemoryCreate(kind=MemoryKind.preference, content="mom and family", pinned=True)
    )
    ctx = await retriever.retrieve("how do I feed the sourdough?")  # unrelated to the pinned memory
    assert any("mom" in item.memory.content for item in ctx.items)  # pinned bypasses the gate
    await store.close()


async def test_stronger_overlap_ranks_first(tmp_path: Path) -> None:
    store, service, retriever = await build_fake_memory_stack(tmp_path, InProcessEventBus())
    await service.add(MemoryCreate(kind=MemoryKind.fact, content="Sourdough starter dough"))
    await service.add(MemoryCreate(kind=MemoryKind.fact, content="connect the circuit"))
    ctx = await retriever.retrieve("sourdough starter dough")
    assert ctx.items
    assert "Sourdough" in ctx.items[0].memory.content  # strongest overlap first
    await store.close()
