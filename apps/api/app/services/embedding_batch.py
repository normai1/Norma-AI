"""
Embedding a whole document or crawl, in pieces that a provider can actually
answer.

Both bulk paths used to hand the provider every chunk in one call - an entire
crawled site's worth in a single request. That works until the site is large,
and then it fails completely: a 200-page crawl produced over a thousand chunks
in one POST, the provider timed out, and the crawl was marked failed with
nothing to show for it. Every page fetched, parsed and chunked, all discarded
because of one request that was always going to be too big.

Batching bounds each request to something answerable. Retrying absorbs the
timeouts that a hosted provider produces anyway - measured at 0.4s warm and
over 5s cold on this project's own configuration, so a slow call in a run of
forty is not an exception, it is expected.

Order is preserved: the caller zips these vectors back against its chunks, so
a reordering here would attach every chunk to the wrong embedding, silently.
"""

import asyncio
import logging

from app.core.config import settings
from app.providers.embedding import (
    EmbeddingProviderError,
    EmbeddingProviderTimeout,
    EmbeddingProviderUnavailable,
)

logger = logging.getLogger(__name__)

# Retried, because they are the provider being slow or briefly unreachable
# rather than the request being wrong. A dimension mismatch is not here: it
# means the configuration disagrees with the model, and retrying an
# incompatible request just fails more slowly.
_RETRYABLE = (EmbeddingProviderTimeout, EmbeddingProviderUnavailable)


async def embed_in_batches(
    embedding_provider,
    texts: list[str],
    *,
    batch_size: int | None = None,
    max_attempts: int | None = None,
) -> list[list[float]]:
    """
    Embed every text, in order, one bounded batch at a time.

    Raises the provider's own error if a batch still fails after its
    attempts - the caller decides what a partial result means, and for a
    knowledge source that is "leave the previous chunk set alone rather than
    replace it with an incomplete one".
    """

    if not texts:
        return []

    size = batch_size or settings.embedding_batch_size
    attempts_allowed = max_attempts or settings.embedding_max_attempts

    vectors: list[list[float]] = []
    batches = [texts[start : start + size] for start in range(0, len(texts), size)]

    for index, batch in enumerate(batches, start=1):
        vectors.extend(
            await _embed_one_batch(
                embedding_provider,
                batch,
                attempts_allowed=attempts_allowed,
                batch_number=index,
                batch_count=len(batches),
            )
        )

    return vectors


async def _embed_one_batch(
    embedding_provider,
    batch: list[str],
    *,
    attempts_allowed: int,
    batch_number: int,
    batch_count: int,
) -> list[list[float]]:
    last_error: EmbeddingProviderError | None = None

    for attempt in range(1, attempts_allowed + 1):
        try:
            return await embedding_provider.embed(batch)
        except _RETRYABLE as exc:
            last_error = exc

            if attempt == attempts_allowed:
                break

            # Backs off so a provider that is rate limiting or cold is given
            # room, rather than being hit again immediately.
            delay = settings.embedding_retry_backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "embedding batch %d/%d failed (attempt %d/%d), retrying in %.1fs: %s",
                batch_number,
                batch_count,
                attempt,
                attempts_allowed,
                delay,
                type(exc).__name__,
            )
            await asyncio.sleep(delay)

    logger.error(
        "embedding batch %d/%d failed after %d attempts",
        batch_number,
        batch_count,
        attempts_allowed,
    )

    raise last_error if last_error is not None else EmbeddingProviderError(
        "embedding failed for an unknown reason",
    )
