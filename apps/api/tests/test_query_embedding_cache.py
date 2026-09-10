"""
The query embedding is the slowest step inside a live turn, and it is a pure
function of (model, text) - so a repeated question should cost nothing.

Reported live as "sometimes it is not responding anything": retrieval was
taking so long that callers concluded the assistant had not heard them and
spoke again, barging in on and cancelling their own pending turn.
"""

import pytest

from app.providers.embedding import EmbeddingProviderTimeout
from app.services.query_embedding_cache import (
    MAX_ENTRIES,
    clear_query_embedding_cache,
    embed_query,
)


class _CountingProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self._fail = fail

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))

        if self._fail:
            raise EmbeddingProviderTimeout("timed out")

        return [[float(len(t)), 0.0, 1.0] for t in texts]


@pytest.fixture(autouse=True)
def _empty_cache():
    clear_query_embedding_cache()
    yield
    clear_query_embedding_cache()


async def test_the_first_call_reaches_the_provider() -> None:
    provider = _CountingProvider()

    vector = await embed_query(provider, "bge", "What are your hours?")

    assert vector == [20.0, 0.0, 1.0]
    assert provider.calls == [["What are your hours?"]]


async def test_the_same_question_asked_again_never_reaches_the_provider() -> None:
    provider = _CountingProvider()

    first = await embed_query(provider, "bge", "What are your hours?")
    second = await embed_query(provider, "bge", "What are your hours?")

    assert first == second
    assert len(provider.calls) == 1


async def test_case_and_surrounding_whitespace_share_an_entry() -> None:
    """
    Two callers asking the same thing rarely produce byte-identical STT
    output; case and trailing space are the differences that carry no
    meaning.
    """

    provider = _CountingProvider()

    await embed_query(provider, "bge", "What are your hours?")
    await embed_query(provider, "bge", "  what are YOUR hours?  ")

    assert len(provider.calls) == 1


async def test_differently_worded_questions_do_not_share_an_entry() -> None:
    """
    The one thing this cache must never do. Punctuation and inner wording
    change what the embedding model returns, so they must miss.
    """

    provider = _CountingProvider()

    await embed_query(provider, "bge", "What are your hours")
    await embed_query(provider, "bge", "What are your hours?")
    await embed_query(provider, "bge", "when do you open")

    assert len(provider.calls) == 3


async def test_a_different_model_does_not_reuse_the_previous_model_s_vector() -> None:
    """
    Changing EMBEDDING_MODEL changes the vector space. Serving the old
    model's vector into a search over newly-embedded chunks would return
    quiet nonsense rather than an error.
    """

    provider = _CountingProvider()

    await embed_query(provider, "bge-base", "What are your hours?")
    await embed_query(provider, "text-embedding-3-small", "What are your hours?")

    assert len(provider.calls) == 2


async def test_the_cache_is_bounded() -> None:
    provider = _CountingProvider()

    for i in range(MAX_ENTRIES + 50):
        await embed_query(provider, "bge", f"question number {i}")

    # The oldest entries were evicted, so asking them again costs a call.
    calls_before = len(provider.calls)
    await embed_query(provider, "bge", "question number 0")

    assert len(provider.calls) == calls_before + 1


async def test_the_most_recently_used_entry_survives_eviction() -> None:
    provider = _CountingProvider()

    await embed_query(provider, "bge", "the popular question")

    for i in range(MAX_ENTRIES - 1):
        await embed_query(provider, "bge", f"question number {i}")
        # Keep the popular one hot, the way a real caller base would.
        await embed_query(provider, "bge", "the popular question")

    calls_before = len(provider.calls)
    await embed_query(provider, "bge", "the popular question")

    assert len(provider.calls) == calls_before


async def test_a_provider_failure_propagates_and_caches_nothing() -> None:
    """
    A timeout must not be remembered as an answer - the next turn has to be
    free to try again.
    """

    failing = _CountingProvider(fail=True)

    with pytest.raises(EmbeddingProviderTimeout):
        await embed_query(failing, "bge", "What are your hours?")

    working = _CountingProvider()
    await embed_query(working, "bge", "What are your hours?")

    assert len(working.calls) == 1
