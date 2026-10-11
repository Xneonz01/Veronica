"""MemoryRetriever: Tier-1 profile + gated hybrid retrieval.

The Tier-1 profile (small, always relevant) is loaded deterministically and bypasses
the gate. Tier-2/3 memories are pooled from semantic (sqlite-vec KNN) and lexical
(token-overlap) candidates, scored by a blend of semantic, lexical, recency and
importance (plus a pinned bonus), and gated: a memory with no real semantic or
lexical signal for the query is dropped, so recency or importance alone can't pull
an off-topic memory into an unrelated turn.

Semantic cosine is rescaled against a baseline before gating, because e5's cosine
for unrelated text is high (~0.72); a raw absolute floor would gate nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.core.config_memory import MemorySettings
from app.services.memory.models import Memory, RetrievedContext, RetrievedMemory
from app.services.memory.normalize import content_tokens
from app.services.memory.store import MemoryStore


class _QueryEmbedder(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...


def rescale_cosine(cosine: float, baseline: float) -> float:
    """Map raw cosine to 0..1 above the unrelated-text baseline.

    e5's cosine for unrelated text is high (~0.72), so a raw absolute floor gates
    nothing. Rescaling against the baseline restores a usable 0..1 relevance signal.
    Shared by the memory retriever and the RAG grounding gate so both judge
    relevance on the same scale.
    """
    if cosine <= baseline:
        return 0.0
    return min(1.0, (cosine - baseline) / (1.0 - baseline))


class MemoryRetriever:
    """Assemble the gated, token-budgeted memory context for a query."""

    def __init__(
        self, store: MemoryStore, embedder: _QueryEmbedder, settings: MemorySettings
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._s = settings

    async def retrieve(self, query: str, *, token_budget: int | None = None) -> RetrievedContext:
        profile = await self._store.profile.get_profile()
        tiers = ["profile"] if profile else []
        query = query.strip()
        if not query:  # welcome-back / no signal -> Tier-1 only (deterministic)
            return RetrievedContext(profile=profile, items=[], tiers_used=tiers)

        query_vec = await self._embedder.embed_query(query)
        query_tokens = content_tokens(query)
        semantic = await self._store.vectors.knn(query_vec, self._s.knn_pool)
        distance_by_id = {mem.id: dist for mem, dist in semantic}
        pool: dict[int, Memory] = {mem.id: mem for mem, _ in semantic}
        for mem in await self._store.vectors.lexical_candidates(query_tokens, self._s.knn_pool):
            pool.setdefault(mem.id, mem)

        scored = [
            self._score(mem, distance_by_id.get(mem.id), query_tokens) for mem in pool.values()
        ]
        kept = [r for r in scored if r is not None]
        kept.sort(key=lambda r: r.score, reverse=True)
        selected = self._apply_caps(kept, token_budget or self._s.token_budget)
        if selected:
            tiers.append("memory")
            await self._store.bump_access([r.memory.id for r in selected])
        return RetrievedContext(profile=profile, items=selected, tiers_used=tiers)

    def _score(
        self, mem: Memory, distance: float | None, query_tokens: set[str]
    ) -> RetrievedMemory | None:
        semantic = self._rescale(1.0 - distance) if distance is not None else 0.0
        lexical = self._lexical_overlap(query_tokens, mem)
        # The relevance gate: no real signal -> drop, unless pinned.
        if not mem.pinned and max(semantic, lexical) < self._s.relevance_floor:
            return None
        score = (
            self._s.w_semantic * semantic
            + self._s.w_lexical * lexical
            + self._s.w_recency * self._recency(mem)
            + self._s.w_importance * mem.importance
        )
        if mem.pinned:
            score += self._s.pinned_bonus
        return RetrievedMemory(memory=mem, score=score, semantic=semantic, lexical=lexical)

    def _rescale(self, cosine: float) -> float:
        return rescale_cosine(cosine, self._s.semantic_baseline)

    @staticmethod
    def _lexical_overlap(query_tokens: set[str], mem: Memory) -> float:
        mem_tokens = content_tokens(mem.content)
        if not query_tokens or not mem_tokens:
            return 0.0
        return len(query_tokens & mem_tokens) / len(query_tokens)

    def _recency(self, mem: Memory) -> float:
        reference = mem.last_accessed_at or mem.created_at
        age_days = (datetime.now(UTC) - reference).total_seconds() / 86400.0
        return float(0.5 ** (age_days / self._s.recency_half_life_days))

    def _apply_caps(
        self, scored: list[RetrievedMemory], token_budget: int
    ) -> list[RetrievedMemory]:
        selected: list[RetrievedMemory] = []
        per_kind: dict[str, int] = {}
        used = 0
        for item in scored:
            if len(selected) >= self._s.max_items:
                break
            kind = item.memory.kind.value
            if per_kind.get(kind, 0) >= self._s.per_kind_cap:
                continue
            cost = max(1, len(item.memory.content) // 4)  # ~4 chars per token
            if used + cost > token_budget:
                continue
            selected.append(item)
            per_kind[kind] = per_kind.get(kind, 0) + 1
            used += cost
        return selected
