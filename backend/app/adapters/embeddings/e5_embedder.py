"""Local multilingual embedding adapter (multilingual-e5-base).

Implements ``EmbeddingProvider``. e5 models expect an instruction prefix,
``query:`` for search queries and ``passage:`` for stored content, and retrieval
quality drops noticeably without it. The model is loaded once through the shared
GPU model manager and encodes off the event loop. Output vectors are L2-normalized
(768-dim for e5-base), so cosine similarity equals the dot product.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import numpy.typing as npt

from app.adapters import model_files
from app.adapters.base import EmbeddingProvider
from app.core.config_memory import MemorySettings
from app.core.gpu_manager import GpuModelManager, ModelLoader

EMBED_MODEL_ID = "embedding"


def embedding_loader(settings: MemorySettings) -> ModelLoader:
    """Loader the GPU manager calls to construct the sentence-transformers model."""

    def load() -> object:
        from sentence_transformers import SentenceTransformer

        # From its folder on disk: given a name, it and transformers asked the hub about
        # it, `local_files_only` or not (`model_files`).
        folder = model_files.embedder_path(settings.embedding_model)
        return SentenceTransformer(folder, device=settings.embedding_device)

    return load


class E5Embedder(EmbeddingProvider):
    """multilingual-e5 embeddings with query/passage prefixes; GPU-resident."""

    def __init__(self, manager: GpuModelManager, model_id: str = EMBED_MODEL_ID) -> None:
        self._manager = manager
        self._model_id = model_id
        # One shared SentenceTransformer handle; serialize encodes so two callers
        # (a REST edit, the post-call learning loop, RAG indexing, a query) never run
        # concurrent forward passes on the same CUDA module.
        self._lock = asyncio.Lock()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed stored content (the passage side of e5)."""
        if not texts:
            return []
        return await self._encode([f"passage: {text}" for text in texts])

    async def embed_query(self, text: str) -> list[float]:
        """Embed a retrieval query (the query side of e5)."""
        vectors = await self._encode([f"query: {text}"])
        return vectors[0]

    async def _encode(self, inputs: list[str]) -> list[list[float]]:
        model = await self._manager.get(self._model_id)
        async with self._lock:
            return await asyncio.to_thread(self._encode_sync, model, inputs)

    @staticmethod
    def _encode_sync(model: Any, inputs: list[str]) -> list[list[float]]:
        # `model` is the GPU-managed SentenceTransformer handle (untyped lib).
        raw = model.encode(inputs, normalize_embeddings=True)
        arr: npt.NDArray[np.float32] = np.asarray(raw, dtype=np.float32)
        return [[float(value) for value in row] for row in arr]
