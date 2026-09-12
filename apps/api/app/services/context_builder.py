"""
Pure context assembly: packs ranked retrieval results into a single
bounded prompt string. No database, no provider - a plain function over
RetrievedChunk values.
"""

from app.services.retrieval import RetrievedChunk

MAX_CONTEXT_CHARS = 4000

_SEPARATOR = "\n\n"


def chunks_that_fit(
    chunks: list[RetrievedChunk], *, max_chars: int = MAX_CONTEXT_CHARS
) -> list[RetrievedChunk]:
    """
    The ranked chunks that actually reach the model, by the same rule
    build_context packs them with.

    Split out so the decision can be reported as well as applied. A chunk
    that was retrieved and then dropped for space is indistinguishable, from
    outside, from one that was never retrieved - and when an answer is wrong
    those are completely different problems.
    """

    kept: list[RetrievedChunk] = []
    total_len = 0

    for chunk in chunks:
        separator_len = len(_SEPARATOR) if kept else 0
        candidate_len = total_len + separator_len + len(chunk.text)

        if candidate_len > max_chars:
            break

        kept.append(chunk)
        total_len = candidate_len

    return kept


def build_context(
    chunks: list[RetrievedChunk], *, max_chars: int = MAX_CONTEXT_CHARS
) -> str:
    """
    Joins chunk texts in the given (ranked) order, stopping at the first
    chunk that would exceed max_chars - a chunk that does not fit is
    dropped whole, never truncated mid-chunk, and no lower-ranked chunk is
    considered ahead of it just because it happens to be smaller.
    """

    return _SEPARATOR.join(
        chunk.text for chunk in chunks_that_fit(chunks, max_chars=max_chars)
    )
