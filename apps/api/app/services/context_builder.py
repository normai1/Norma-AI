"""
Pure context assembly: packs ranked retrieval results into a single
bounded prompt string. No database, no provider - a plain function over
RetrievedChunk values.
"""

from app.services.retrieval import RetrievedChunk

MAX_CONTEXT_CHARS = 4000

_SEPARATOR = "\n\n"


def build_context(
    chunks: list[RetrievedChunk], *, max_chars: int = MAX_CONTEXT_CHARS
) -> str:
    """
    Joins chunk texts in the given (ranked) order, stopping at the first
    chunk that would exceed max_chars - a chunk that does not fit is
    dropped whole, never truncated mid-chunk, and no lower-ranked chunk is
    considered ahead of it just because it happens to be smaller.
    """

    parts: list[str] = []
    total_len = 0

    for chunk in chunks:
        separator_len = len(_SEPARATOR) if parts else 0
        candidate_len = total_len + separator_len + len(chunk.text)

        if candidate_len > max_chars:
            break

        parts.append(chunk.text)
        total_len = candidate_len

    return _SEPARATOR.join(parts)
