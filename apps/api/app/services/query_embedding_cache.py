"""
A small in-process cache of query embeddings, for CLAUDE.md section 11's
"caching embeddings for repeated caller phrasings".

Only the retrieval path uses this. Ingestion embeds each chunk of a document
exactly once, so a cache there would grow without ever being read; callers,
by contrast, ask the same handful of questions in almost the same words all
day - opening hours, location, price, "are you open on Sunday".

Caching is safe here in a way it usually is not: an embedding is a pure
function of (model, text). There is nothing to invalidate and nothing can go
stale. The only cost is memory, which the bounded size below fixes: 512
entries of 768 floats is on the order of a few megabytes.

Keyed on the model as well as the text so that changing EMBEDDING_MODEL
cannot serve vectors from the previous model into a search against
differently-embedded chunks.
"""

import re
import unicodedata
from collections import OrderedDict

from app.providers.embedding import EmbeddingProvider

MAX_ENTRIES = 512

# Spoken filler that carries no meaning but does move the query's embedding.
#
# Measured on a real 4,466-chunk corpus, twelve questions the site answers,
# each asked in colloquial phrasing: prefixing "um so like" cost 0.025 of
# rank-1 similarity on average, and pushed two more questions below
# retrieval_min_score. The words contribute nothing to what was asked and
# the embedding has no way to know that, so they are removed before it is
# computed.
#
# Leading position only, and a deliberately conservative list. A filler word
# in the middle of a sentence is frequently not filler at all - "is it ok to
# bring a dog", "well water pressure", "the right side" - and a retrieval
# path must not quietly rewrite what the caller asked. Anything ambiguous is
# left in; the cost of missing one is a fraction of a similarity point,
# while the cost of eating a real word is a wrong answer.
_LEADING_FILLERS = (
    "um",
    "umm",
    "uhm",
    "uh",
    "uhh",
    "erm",
    "er",
    "ah",
    "hmm",
    "hm",
    "mm",
    "mmm",
    "like",
    "so",
    "actually",
    "basically",
    "i mean",
    "you know",
    "let me see",
    "let's see",
)

_FILLER_PATTERN = re.compile(
    r"^(?:" + "|".join(re.escape(word) for word in _LEADING_FILLERS) + r")\b[\s,]*",
    re.IGNORECASE,
)


def strip_fillers(query: str) -> str:
    """
    The query with leading spoken filler removed.

    Applied repeatedly, because real speech stacks it - "um so like how
    much" is three in a row. Returns the original whenever stripping would
    leave nothing: a caller who said only "um" asked something the rest of
    the turn has to handle, and an empty string embeds to noise.
    """

    original = query.strip()
    stripped = original

    while True:
        shorter = _FILLER_PATTERN.sub("", stripped, count=1).strip()

        if shorter == stripped:
            break

        if not shorter:
            # Every word was filler. Returning the last fragment instead -
            # "like", from "um so like" - would embed one arbitrary filler
            # word and look like a real query; the original at least stays
            # faithful to what was said.
            return original

        stripped = shorter

    return stripped or original

_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()


def _key(model: str, query: str) -> tuple[str, str]:
    """
    Normalize away the differences that do not change what was asked, so
    "What are your hours?" and "what are your hours?" share an entry.
    Deliberately conservative: case and surrounding whitespace only. Anything
    more aggressive (stripping punctuation, collapsing inner whitespace)
    starts merging questions the embedding model would place in different
    places, which is the one thing this cache must never do.
    """

    return (model, unicodedata.normalize("NFC", strip_fillers(query).casefold()))


def is_query_cached(model: str, query: str) -> bool:
    """
    Whether the next embed_query for this query would be answered from
    cache.

    Read-only, and deliberately does not touch the LRU order: this exists so
    a trace can say whether a turn paid the hosted provider or not (item
    25a), and an observability probe that promoted an entry would be
    changing what it measures.
    """

    return _key(model, query) in _cache


async def embed_query(
    provider: EmbeddingProvider, model: str, query: str
) -> list[float]:
    """
    Return the embedding of `query`, from cache when it has been asked
    before. Provider errors propagate unchanged - a failed call caches
    nothing and is retried on the next turn.
    """

    key = _key(model, query)
    cached = _cache.get(key)

    if cached is not None:
        _cache.move_to_end(key)
        return cached

    # The filler-stripped form, matching what the key above was built from -
    # embedding the raw text while keying on the stripped one would serve
    # one caller's vector to a different question.
    [vector] = await provider.embed([strip_fillers(query)])

    _cache[key] = vector
    _cache.move_to_end(key)

    while len(_cache) > MAX_ENTRIES:
        _cache.popitem(last=False)

    return vector


async def warm_query_embeddings(
    provider: EmbeddingProvider, model: str, queries: list[str]
) -> int:
    """
    Embed every one of `queries` not already cached, in a single provider
    call, and return how many were added.

    This is what makes the cache worth having on the first call of the day
    rather than the fiftieth. The hosted embedding router is bimodal -
    measured at 0.28-0.43s most of the time with roughly one call in three
    taking 4-12s - and the media plane will not wait that long inside a
    turn, so an uncached question is a question answered without knowledge.
    An assistant's own FAQ questions are exactly the phrasings callers use,
    they are already sitting in the database, and there are tens of them,
    not thousands: one batched call before the first turn converts most of
    the misses into 60ms hits.

    CLAUDE.md section 11 asks for this directly - preloading the
    assistant's highest-frequency FAQ content rather than retrieving it.
    """

    missing = []
    seen = set()

    for query in queries:
        key = _key(model, query)

        if key in _cache or key in seen:
            continue

        seen.add(key)
        # Stripped, for the same reason embed_query embeds the stripped
        # form: the key below is built from it, and caching a vector of one
        # string under the key of another is how a warm-up starts answering
        # the wrong question. FAQ text rarely contains filler, so in
        # practice this changes nothing and costs nothing - but the two
        # paths agreeing is what stops it mattering later.
        missing.append(strip_fillers(query))

    if not missing:
        return 0

    vectors = await provider.embed(missing)

    for query, vector in zip(missing, vectors, strict=True):
        _cache[_key(model, query)] = vector
        _cache.move_to_end(_key(model, query))

    while len(_cache) > MAX_ENTRIES:
        _cache.popitem(last=False)

    return len(missing)


def clear_query_embedding_cache() -> None:
    """
    Drop every entry. For tests, and for anything that reconfigures the
    embedding provider inside a running process.
    """

    _cache.clear()

