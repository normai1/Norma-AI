"""
Deterministic embedding provider mock. No network - exists so the test suite
never depends on a paid or live external API.
"""

import hashlib
import random

from app.providers.embedding import EmbeddingProviderError


class MockEmbeddingProvider:
    """
    Embeds a text into a deterministic pseudo-random vector seeded from its
    own content - identical text always yields an identical vector, distinct
    text (almost certainly) yields a distinct one. embedded_texts records
    every text passed to embed(), across every call, for a test to inspect
    directly. failure, if set, is raised instead of embedding anything.
    """

    def __init__(
        self,
        *,
        dimension: int = 1536,
        failure: EmbeddingProviderError | None = None,
    ) -> None:
        self._dimension = dimension
        self.failure = failure
        self.embedded_texts: list[str] = []

    def _vector_for(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest(), "big")
        rng = random.Random(seed)

        return [rng.uniform(-1.0, 1.0) for _ in range(self._dimension)]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if self.failure is not None:
            raise self.failure

        self.embedded_texts.extend(texts)

        return [self._vector_for(text) for text in texts]
