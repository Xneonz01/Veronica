"""Real multilingual-e5-base smoke (GPU). Marked slow; excluded from the default run."""

from __future__ import annotations

import pytest

from app.adapters.embeddings.e5_embedder import EMBED_MODEL_ID, E5Embedder, embedding_loader
from app.core.config import get_settings
from app.core.gpu_manager import CudaGpuModelManager, GpuMode, gib

pytestmark = pytest.mark.slow


async def test_real_e5_embeds_768_and_ranks_arabic() -> None:
    settings = get_settings()
    manager = CudaGpuModelManager(budget_gib=gib(settings.gpu.vram_budget_gb))
    manager.register(
        EMBED_MODEL_ID,
        embedding_loader(settings.memory),
        {GpuMode.idle},
        settings.memory.embedding_vram_gb,
    )
    embedder = E5Embedder(manager)

    passages = await embedder.embed(["My name is Rowan.", "The weather is cold."])
    assert len(passages) == 2
    assert len(passages[0]) == settings.memory.embedding_dim  # 768

    query = await embedder.embed_query("ما اسمي؟")  # "what is my name?" (Arabic)

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert dot(query, passages[0]) > dot(query, passages[1])  # name nearer than weather
    await manager.unload(EMBED_MODEL_ID)
