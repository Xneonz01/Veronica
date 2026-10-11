"""Her memory's settings: embeddings, retrieval, documents and learning. A section of
`config.Settings` (`memory`), kept in its own file like `config_llm.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.paths import Place


class MemorySettings(BaseModel):
    """Conversational memory, embeddings, retrieval, RAG and learning."""

    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_device: str = "cuda"
    embedding_dim: int = 768
    embedding_vram_gb: float = 1.5
    # Loaded in the background at start (4.0 to 4.5 s from disk, measured), so the first
    # message doesn't wait for it; idle mode keeps it resident anyway. Off in the tests.
    warm_on_start: bool = True
    # Where it is when this section is built alone; in `Settings`, in the data folder
    # unless set (`config.STORES`).
    db_path: Place = "data/memory/memory.db"
    # Retrieval + relevance gate. Floors are calibrated to e5-base, whose cosine
    # for unrelated text sits ~0.72; similarity is rescaled above that baseline
    # to a 0..1 signal before gating (a naive absolute floor would gate nothing).
    knn_pool: int = 50  # semantic candidate pool fetched from sqlite-vec
    semantic_baseline: float = 0.72
    relevance_floor: float = 0.18  # on max(rescaled-semantic, lexical); drop below unless pinned
    recency_half_life_days: float = 14.0
    w_semantic: float = 0.55
    w_lexical: float = 0.20
    w_recency: float = 0.15
    w_importance: float = 0.10
    pinned_bonus: float = 0.5
    max_items: int = 8  # cap on injected memories per turn
    per_kind_cap: int = 4
    token_budget: int = 512
    # RAG / knowledge documents, separate from conversational memory.
    chunk_target_chars: int = 800
    chunk_overlap_chars: int = 120
    # Bounded well under sqlite-vec's limit of 4,096 nearest neighbours, which a larger
    # value would pass on to every search and fail it.
    rag_top_k: int = Field(default=5, ge=1, le=100)
    # Keyword search, for a question that names an identifier: how many chunks it and the
    # search by meaning each bring to the fused ranking (`RagService.retrieve`). 0: off.
    rag_keyword_depth: int = Field(default=20, ge=0, le=500)
    # Ground only on chunks whose rescaled cosine clears this floor (same scale as
    # relevance_floor). e5's unrelated-text cosine is ~0.72, so a raw distance gate
    # cites everything; rescaling plus a floor of 0.25 keeps real matches, drops noise.
    rag_relevance_floor: float = 0.25
    # Reranking (`rag_rerank.py`): a cross-encoder reads this many candidates beside the
    # question and orders them, and a reranked chunk is cited only if its score (a
    # logit) clears `rag_rerank_floor` instead of the floor above. 0: off. Measured on
    # the retrieval evaluation: 10 deep ranks as well as 30 at a third of the time, and
    # -7.5 sits between the answers (-6.6 and up, but for two) and seven of the ten
    # unanswerable questions (-8.0 and below).
    rag_rerank_depth: int = Field(default=10, ge=0, le=100)
    rag_rerank_floor: float = -7.5
    # Token cap for the grounding block, separate from the memory `token_budget`.
    rag_token_budget: int = 768
    # Controlled learning: fact extraction after a call, and from chat once the model is free.
    learning_mode: str = "proactive"  # proactive | review_only (live-settable at runtime)
    learning_confidence_threshold: float = 0.7  # proactive auto-store cutoff
    learning_max_facts: int = 8  # cap on facts stored per extraction (anti-runaway)
    # Chat is learned from later (`chat/learning_worker.py`), once no call is live and
    # nothing has used the model for this long; it gives way when a reply needs the model.
    learning_quiet_s: float = Field(default=300.0, ge=0.0)
    # How freely the model writes what it learned: lower than chat's, since an
    # extraction is a record, not a reply. Measured on qwen3.5:9b (2026-09-26): at
    # chat's 0.7, 8 extractions in 60 of three plain facts came back empty; at 0.2,
    # 1 in 60, and the learning test filed 13, 13 and 12 of its 13 facts with no false
    # memory (0.30.2's last three runs at 0.7: 11 each). Another model should be
    # measured again (`scripts/model_eval.py --families memory`).
    learning_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    # Hard ceiling on active, non-pinned, non-profile memories. Over it, the least
    # recently used are archived (decay by disuse) so the store can't grow unbounded
    # and flood the relevance gate. Tier-1 profile and pinned memories are never pruned.
    max_active_memories: int = 200
