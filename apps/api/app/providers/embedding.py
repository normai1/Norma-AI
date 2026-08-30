"""
Embedding provider contract: the interface every text-embedding
implementation in this codebase is built against - mock (tests), OpenAI
(production). Mirrors app/providers/speech.py's shape.
"""

from typing import Protocol


class EmbeddingProviderError(Exception):
    """
    Base class for an embedding provider's own failures, distinct from a bug
    in the calling code.
    """


class EmbeddingProviderTimeout(EmbeddingProviderError):
    """
    The provider did not respond within the caller's bound.
    """


class EmbeddingProviderUnavailable(EmbeddingProviderError):
    """
    The provider rejected the request, or the connection could not be
    established - auth failure, outage, or rate limit.
    """


class EmbeddingDimensionMismatch(EmbeddingProviderError):
    """
    The provider returned a different number of vectors than texts given, or
    a vector whose length does not match the configured dimension. Always a
    hard failure - CLAUDE.md section 6.4 forbids silently truncating or
    padding an embedding to fit.
    """


class EmbeddingProvider(Protocol):
    """
    Batch text embedding. One vector per input text, in the same order.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed each text in texts, returning one vector per text in the same
        order, each exactly settings.embedding_dimension floats long. An
        empty texts list returns an empty list without contacting the
        provider.
        """
        ...
