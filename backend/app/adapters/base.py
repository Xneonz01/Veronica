"""Abstract interfaces for model providers.

Concrete adapters live in the subpackages. `E5Embedder` subclasses
`EmbeddingProvider` and `SmartTurnArbiter` subclasses `TurnArbiter`; the STT, LLM and
TTS adapters implement the matching methods without subclassing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import numpy.typing as npt


class STTProvider(ABC):
    """Speech-to-text provider."""

    @abstractmethod
    async def transcribe_stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]:
        """Transcribe a stream of audio chunks into a stream of text."""


class LLMProvider(ABC):
    """Large-language-model provider with optional native tool-calling."""

    @abstractmethod
    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[str]:
        """Stream generated tokens for the given chat messages."""


class TTSProvider(ABC):
    """Text-to-speech provider."""

    @abstractmethod
    async def synthesize_stream(self, text: str, voice_id: str) -> AsyncIterator[bytes]:
        """Synthesize text into a stream of audio chunks."""


class TurnArbiter(ABC):
    """End-of-turn provider: has the speaker finished, or only paused?"""

    @abstractmethod
    def probability(self, audio: npt.NDArray[np.float32]) -> float:
        """P(the turn is complete) for 16 kHz mono float32 audio, in [0, 1].

        Synchronous: it is called from the voice-detection thread, never the event loop.
        """


class EmbeddingProvider(ABC):
    """Text embedding provider for retrieval."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors."""


class Reranker(ABC):
    """Relevance provider: how well does each passage answer the question?"""

    @abstractmethod
    def scores(self, question: str, passages: list[str]) -> list[float]:
        """One score per passage, in order, higher for a better answer. Blocking (the
        model runs on the CPU), so callers run it on a thread."""
