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

import unicodedata
from collections import OrderedDict

from app.providers.embedding import EmbeddingProvider

MAX_ENTRIES = 512

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

    return (model, unicodedata.normalize("NFC", query.strip().casefold()))


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

    [vector] = await provider.embed([query])

    _cache[key] = vector
    _cache.move_to_end(key)

    while len(_cache) > MAX_ENTRIES:
        _cache.popitem(last=False)

    return vector


def clear_query_embedding_cache() -> None:
    """
    Drop every entry. For tests, and for anything that reconfigures the
    embedding provider inside a running process.
    """

    _cache.clear()
