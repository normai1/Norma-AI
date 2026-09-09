"""
Large crawls have to survive a slow embedding provider.

The failure this covers happened for real: a 200-page crawl of a documentation
site produced over a thousand chunks, handed them to the provider in one
request, timed out, and was marked failed with zero chunks written. Every page
fetched, parsed and chunked, all discarded - and while it ran it saturated the
API process for half an hour.
"""

import pytest

from app.providers.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingProviderTimeout,
    EmbeddingProviderUnavailable,
)
from app.services.embedding_batch import embed_in_batches


class _RecordingProvider:
    """Records the shape of every call so batching can be asserted on."""

    def __init__(
        self,
        *,
        fail_batches: set[int] | None = None,
        error=None,
        dimension: int = 3,
    ):
        self.calls: list[list[str]] = []
        self._fail_batches = fail_batches or set()
        self._error = error or EmbeddingProviderTimeout("timed out")
        self._dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))

        if len(self.calls) in self._fail_batches:
            raise self._error

        return [[float(len(t))] * self._dimension for t in texts]


async def test_no_texts_never_contacts_the_provider() -> None:
    provider = _RecordingProvider()

    assert await embed_in_batches(provider, []) == []
    assert provider.calls == []


async def test_a_large_input_is_split_into_bounded_requests() -> None:
    """
    The headline fix: one request per batch, not one request for everything.
    """

    provider = _RecordingProvider()
    texts = [f"chunk {i}" for i in range(250)]

    vectors = await embed_in_batches(provider, texts, batch_size=32)

    assert len(vectors) == 250
    assert len(provider.calls) == 8
    assert all(len(call) <= 32 for call in provider.calls)


async def test_vectors_come_back_in_the_original_order() -> None:
    """
    The caller zips these against its chunks, so a reordering here would
    attach every chunk to the wrong embedding - silently, and only visible
    later as retrieval returning nonsense.
    """

    provider = _RecordingProvider()
    texts = ["a", "bb", "ccc", "dddd", "eeeee", "ffffff", "ggggggg"]

    vectors = await embed_in_batches(provider, texts, batch_size=2)

    # The stub encodes each text's length, so order is checkable.
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


async def test_a_timed_out_batch_is_retried_rather_than_losing_the_crawl() -> None:
    """
    A hosted provider times out often enough that one slow call in a run of
    forty is expected. Before retries, that one call discarded everything.
    """

    provider = _RecordingProvider(fail_batches={2})
    texts = [f"chunk {i}" for i in range(6)]

    vectors = await embed_in_batches(
        provider, texts, batch_size=2, max_attempts=3
    )

    assert len(vectors) == 6
    # Batch 2 was attempted twice; four calls for three batches.
    assert len(provider.calls) == 4


async def test_an_unavailable_provider_is_also_retried() -> None:
    provider = _RecordingProvider(
        fail_batches={1}, error=EmbeddingProviderUnavailable("502")
    )

    vectors = await embed_in_batches(provider, ["a", "b"], batch_size=2, max_attempts=2)

    assert len(vectors) == 2
    assert len(provider.calls) == 2


async def test_a_batch_that_never_succeeds_raises_so_the_caller_can_decide() -> None:
    """
    Not swallowed: the crawl path deliberately leaves the previous chunk set
    alone rather than replacing it with an incomplete one, and it can only do
    that if it hears about the failure.
    """

    provider = _RecordingProvider(fail_batches={1, 2, 3})

    with pytest.raises(EmbeddingProviderTimeout):
        await embed_in_batches(provider, ["a", "b"], batch_size=2, max_attempts=3)

    assert len(provider.calls) == 3


async def test_a_dimension_mismatch_is_not_retried() -> None:
    """
    The configuration disagrees with the model; retrying an incompatible
    request just fails more slowly.
    """

    provider = _RecordingProvider(
        fail_batches={1}, error=EmbeddingDimensionMismatch("768 != 1536")
    )

    with pytest.raises(EmbeddingDimensionMismatch):
        await embed_in_batches(provider, ["a"], batch_size=1, max_attempts=3)

    assert len(provider.calls) == 1
