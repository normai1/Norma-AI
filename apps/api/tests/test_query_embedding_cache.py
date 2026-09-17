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
    warm_query_embeddings,
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
    await embed_query(provider, "BAAI/bge-base-en-v1.5", "What are your hours?")

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


async def test_warming_embeds_every_question_in_one_call() -> None:
    """
    One request regardless of how many questions the assistant has - the
    point is to spend the provider's unpredictable seconds once, before the
    conversation starts, not once per question.
    """

    provider = _CountingProvider()
    questions = [f"question number {i}" for i in range(40)]

    warmed = await warm_query_embeddings(provider, "bge", questions)

    assert warmed == 40
    assert len(provider.calls) == 1
    assert provider.calls[0] == questions


async def test_a_warmed_question_costs_nothing_when_the_caller_asks_it() -> None:
    """
    The whole point: the caller's turn is a cache hit, so retrieval is a
    database lookup rather than a call to the hosted embedding router.
    """

    provider = _CountingProvider()

    await warm_query_embeddings(provider, "bge", ["What are your hours?"])
    calls_after_warming = len(provider.calls)

    await embed_query(provider, "bge", "what are your HOURS?")

    assert len(provider.calls) == calls_after_warming


async def test_warming_skips_questions_that_are_already_cached() -> None:
    provider = _CountingProvider()

    await embed_query(provider, "bge", "What are your hours?")
    warmed = await warm_query_embeddings(
        provider, "bge", ["What are your hours?", "Where are you?"]
    )

    assert warmed == 1
    assert provider.calls[-1] == ["Where are you?"]


async def test_warming_deduplicates_within_one_batch() -> None:
    """
    Two knowledge sources can easily generate the same question. Sending it
    twice would waste provider work and, worse, risk zipping the results
    back onto the wrong texts.
    """

    provider = _CountingProvider()

    warmed = await warm_query_embeddings(
        provider, "bge", ["Are you open?", "are you open?", "Where are you?"]
    )

    assert warmed == 2
    assert provider.calls == [["Are you open?", "Where are you?"]]


async def test_warming_nothing_never_contacts_the_provider() -> None:
    provider = _CountingProvider()

    assert await warm_query_embeddings(provider, "bge", []) == 0
    assert provider.calls == []


async def test_warming_stays_within_the_cache_bound() -> None:
    provider = _CountingProvider()

    await warm_query_embeddings(
        provider, "bge", [f"question number {i}" for i in range(MAX_ENTRIES + 100)]
    )

    from app.services.query_embedding_cache import _cache

    assert len(_cache) == MAX_ENTRIES


async def test_warming_keeps_each_question_matched_to_its_own_vector() -> None:
    """
    A batched call returns a list; mis-zipping it would attach every FAQ
    question to another question's vector, and retrieval would return
    confident nonsense rather than an error.
    """

    provider = _CountingProvider()
    questions = ["a", "bb", "ccc"]

    await warm_query_embeddings(provider, "bge", questions)

    for question in questions:
        # The stub encodes each text's length in its vector.
        assert await embed_query(provider, "bge", question) == [
            float(len(question)),
            0.0,
            1.0,
        ]


# ----------------------------------------------------------------------
# Spoken filler
#
# Measured on a real corpus: prefixing "um so like" to a colloquially-phrased
# question cost 0.025 of rank-1 similarity and pushed two of twelve
# answerable questions below retrieval_min_score. The words mean nothing and
# the embedding cannot know that.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("um how much does it cost", "how much does it cost"),
        ("uh what are your hours", "what are your hours"),
        ("um so like how much is it", "how much is it"),
        ("erm, can I book a table", "can I book a table"),
        ("you know what time do you close", "what time do you close"),
        ("i mean are you open sunday", "are you open sunday"),
        ("  so   what do you charge  ", "what do you charge"),
    ],
)
def test_leading_filler_is_removed(spoken: str, expected: str) -> None:
    from app.services.query_embedding_cache import strip_fillers

    assert strip_fillers(spoken) == expected


@pytest.mark.parametrize(
    "query",
    [
        # Each of these contains a filler word that is not filler here.
        "is it ok to like a post",
        "do you sell well water pumps",
        "what is the actually reserved seat policy",
        "how do I mm convert the file",
        # Nothing to strip.
        "what are your opening hours",
        # Stripping everything would leave nothing to embed.
        "um",
        "um so like",
    ],
)
def test_meaningful_words_survive(query: str) -> None:
    """
    A retrieval path must never quietly rewrite what the caller asked. The
    list is leading-position-only and conservative for this reason: missing
    a filler costs a fraction of a similarity point, eating a real word
    costs a wrong answer.
    """

    from app.services.query_embedding_cache import strip_fillers

    # Unchanged in every case: the filler words here are not in leading
    # position, or there is nothing to strip, or stripping would leave
    # nothing to embed and the original is kept instead.
    assert strip_fillers(query) == query.strip()


async def test_filler_variants_share_one_cached_embedding() -> None:
    """
    "how much is it" and "um so like how much is it" are the same question,
    so the second must not pay the provider again - and must not receive a
    vector computed from different text than the one it was keyed under.
    """

    provider = _CountingProvider()

    first = await embed_query(provider, "m", "how much is it")
    second = await embed_query(provider, "m", "um so like how much is it")

    assert first == second
    assert provider.calls == [["how much is it"]]


async def test_the_embedded_text_is_the_text_the_key_was_built_from() -> None:
    """
    Regression guard: keying on the stripped form while embedding the raw
    one caches a vector of one string under another string's key, which is
    how a cache starts answering the wrong question.
    """

    provider = _CountingProvider()

    await embed_query(provider, "m", "uh what are your hours")

    assert provider.calls == [["what are your hours"]]


async def test_warming_embeds_what_it_keys() -> None:
    provider = _CountingProvider()

    await warm_query_embeddings(provider, "m", ["um do you deliver"])

    assert provider.calls == [["do you deliver"]]

    # And the warmed entry is found by the un-filled phrasing.
    await embed_query(provider, "m", "do you deliver")

    assert len(provider.calls) == 1
