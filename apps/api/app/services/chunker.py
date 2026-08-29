"""
Pure text-chunking logic: no database, no knowledge of where the text came
from. Splits normalized text into paragraph-aware spans bounded by
max_chars, each carrying its char_start/char_end offset in the original
text for citation. text[char_start:char_end] always equals the span's text.
"""

import re
from dataclasses import dataclass

MAX_CHUNK_CHARS = 1500

_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class ChunkSpan:
    text: str
    char_start: int
    char_end: int


def _strip_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    """
    Trims whitespace from text[start:end], keeping offsets in sync. Returns
    None if nothing but whitespace remains in the span.
    """

    segment = text[start:end]
    left_trim = len(segment) - len(segment.lstrip())
    right_trim = len(segment) - len(segment.rstrip())
    new_start = start + left_trim
    new_end = end - right_trim

    return (new_start, new_end) if new_start < new_end else None


def _split_paragraphs(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0

    for match in _PARAGRAPH_BOUNDARY.finditer(text):
        stripped = _strip_span(text, pos, match.start())
        if stripped is not None:
            spans.append(stripped)
        pos = match.end()

    stripped = _strip_span(text, pos, len(text))
    if stripped is not None:
        spans.append(stripped)

    return spans


def _split_oversized(
    text: str, start: int, end: int, max_chars: int
) -> list[tuple[int, int]]:
    """
    Splits text[start:end] (already known to exceed max_chars) on
    whitespace-word boundaries into offset pairs, so no span is ever
    truncated mid-word. A single word longer than max_chars alone becomes
    its own (oversized) span rather than being torn apart mid-word.
    """

    words = [
        (start + match.start(), start + match.end())
        for match in re.finditer(r"\S+", text[start:end])
    ]

    spans: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end: int | None = None

    for word_start, word_end in words:
        candidate_len = (
            word_end - current_start if current_start is not None else 0
        )

        if current_start is not None and candidate_len > max_chars:
            spans.append((current_start, current_end))
            current_start = None

        if current_start is None:
            current_start = word_start

        current_end = word_end

    if current_start is not None:
        spans.append((current_start, current_end))

    return spans


def chunk_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[ChunkSpan]:
    """
    Splits normalized text into paragraph-aware spans up to max_chars.
    Consecutive paragraphs are packed into one span while they fit; a
    single paragraph longer than max_chars falls back to a whitespace-
    boundary split. Blank/empty text returns an empty list.
    """

    if not text.strip():
        return []

    spans: list[ChunkSpan] = []
    current_start: int | None = None
    current_end: int | None = None

    def flush() -> None:
        nonlocal current_start, current_end
        if current_start is not None:
            spans.append(
                ChunkSpan(text[current_start:current_end], current_start, current_end)
            )
            current_start = None
            current_end = None

    for para_start, para_end in _split_paragraphs(text):
        if para_end - para_start > max_chars:
            flush()
            for sub_start, sub_end in _split_oversized(
                text, para_start, para_end, max_chars
            ):
                spans.append(ChunkSpan(text[sub_start:sub_end], sub_start, sub_end))
            continue

        candidate_len = (
            para_end - current_start if current_start is not None else 0
        )

        if current_start is not None and candidate_len > max_chars:
            flush()

        if current_start is None:
            current_start = para_start

        current_end = para_end

    flush()

    return spans
