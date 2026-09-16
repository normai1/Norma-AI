"""
The chunker's budget is counted in tokens, using the embedding model's own
tokenizer.

Most tests here pass a model name with no Hugging Face tokenizer, which drops
the chunker onto its deterministic character estimate. That keeps them fast
and hermetic - no Hub download, no dependency on what is cached on the machine
running them - while still exercising every branch that matters: the budget,
the offsets, and the overlap. The two tests that need the real tokenizer say
so, and skip when it is unavailable.
"""

import pytest

from app.services.chunker import (
    CHUNK_OVERLAP_TOKENS,
    MAX_CHUNK_TOKENS,
    ChunkSpan,
    _load_tokenizer,
    chunk_text,
    drop_repeated_spans,
)

# Has no tokenizer on the Hub, so _load_tokenizer returns None and the
# character estimate is used - deterministic, and offline.
_NO_TOKENIZER = "not-a-real-model/does-not-exist"

# What that estimate counts, mirroring _FALLBACK_CHARS_PER_TOKEN.
_FALLBACK_CHARS_PER_TOKEN = 3


def _estimated_tokens(text: str) -> int:
    return len(text) // _FALLBACK_CHARS_PER_TOKEN + 1


def _assert_offsets_match(text: str, spans: list) -> None:
    """
    The contract every caller relies on for citation: a span's recorded
    offsets slice the original back to exactly the span's own text.
    """

    for span in spans:
        assert text[span.char_start : span.char_end] == span.text


def test_empty_text_returns_no_spans() -> None:
    assert chunk_text("") == []


def test_whitespace_only_text_returns_no_spans() -> None:
    assert chunk_text("   \n\n  \t \n") == []


def test_short_text_returns_one_span() -> None:
    text = "Our hours are 9am to 5pm, Monday through Friday."

    spans = chunk_text(text, model=_NO_TOKENIZER)

    assert len(spans) == 1
    assert spans[0].text == text
    _assert_offsets_match(text, spans)


def test_short_paragraphs_pack_into_a_single_span_when_they_fit() -> None:
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."

    spans = chunk_text(text, model=_NO_TOKENIZER)

    assert len(spans) == 1
    _assert_offsets_match(text, spans)


def test_spans_are_bounded_by_the_token_budget_not_the_character_count() -> None:
    paragraph = "Paragraph text repeated to take up real space. " * 3
    text = "\n\n".join([paragraph] * 5)

    spans = chunk_text(text, max_tokens=40, overlap_tokens=0, model=_NO_TOKENIZER)

    assert len(spans) > 1
    for span in spans:
        assert _estimated_tokens(span.text) <= 40
    _assert_offsets_match(text, spans)
    # Packing must not drop any paragraph's content.
    assert "".join(span.text for span in spans).count("Paragraph text") == 15


def test_oversized_single_paragraph_falls_back_to_whitespace_split() -> None:
    words = [f"word{i}" for i in range(200)]
    text = " ".join(words)

    spans = chunk_text(text, max_tokens=20, overlap_tokens=0, model=_NO_TOKENIZER)

    assert len(spans) > 1
    for span in spans:
        assert _estimated_tokens(span.text) <= 20
        # No word is ever torn in half.
        assert all(part.startswith("word") for part in span.text.split())
    _assert_offsets_match(text, spans)
    assert " ".join(span.text for span in spans) == text


def test_an_unbroken_run_is_still_split_rather_than_kept_whole() -> None:
    # RecursiveCharacterTextSplitter's last-resort separator is a raw
    # character split, so a run with no paragraph/line/word boundary is still
    # bounded rather than kept as one oversized span.
    text = "x" * 5000

    spans = chunk_text(text, max_tokens=100, overlap_tokens=0, model=_NO_TOKENIZER)

    assert len(spans) > 1
    for span in spans:
        assert _estimated_tokens(span.text) <= 100
    _assert_offsets_match(text, spans)
    assert "".join(span.text for span in spans) == text


def test_adjacent_spans_overlap_so_a_fact_on_a_boundary_survives() -> None:
    """
    With no overlap a fact straddling a boundary lands half in each chunk and
    retrieves well for neither - the usual reason a knowledge base "does not
    contain" something a reader can point to in the source.
    """

    text = " ".join(f"sentence number {i} about the clinic." for i in range(300))

    spans = chunk_text(text, max_tokens=60, overlap_tokens=15, model=_NO_TOKENIZER)

    assert len(spans) > 2
    overlapping = [
        spans[i + 1].char_start < spans[i].char_end for i in range(len(spans) - 1)
    ]
    assert any(overlapping), "no pair of adjacent spans shares any text"
    _assert_offsets_match(text, spans)


def test_offsets_are_exact_even_though_spans_overlap() -> None:
    """
    Regression: the splitter's own add_start_index cannot survive overlap. It
    searches forward from the end of the previous chunk, and an overlapping
    chunk begins before that point, so it reported -1 and char_end landed past
    the end of the text - silently, and only visible to something that
    actually slices the original with the result.
    """

    text = "\n\n".join(f"Section {i}. The clinic opens at nine." for i in range(80))

    spans = chunk_text(text, max_tokens=50, overlap_tokens=12, model=_NO_TOKENIZER)

    assert spans
    for span in spans:
        assert span.char_start >= 0
        assert span.char_end <= len(text)
    _assert_offsets_match(text, spans)


def test_the_defaults_leave_room_for_the_models_special_tokens() -> None:
    """
    bge-base accepts 512 tokens *including* the pair the tokenizer adds, so
    the budget has to sit below that rather than at it.
    """

    assert MAX_CHUNK_TOKENS < 510
    assert 0 < CHUNK_OVERLAP_TOKENS < MAX_CHUNK_TOKENS


def test_chunks_are_not_small_enough_to_stop_discriminating() -> None:
    """
    A lower bound, measured rather than assumed, and the reason it exists.

    The budget was once 128 tokens, on the reasoning that a shorter chunk
    embeds one idea and so retrieves more precisely. Re-chunking a real
    300-page corpus to 128 and re-embedding it showed the opposite: a
    fragment that short is not about anything in particular, so it sits
    weakly near every question, and with ten thousand of them some fragment
    is always close enough. Best-chunk scores for questions the site cannot
    answer rose from 0.414-0.579 to 0.521-0.632 - across
    `retrieval_min_score`, so the model started being handed chunks for
    questions the knowledge does not cover. See chunker.py for the numbers.

    This does not test retrieval quality, which needs embeddings and a
    corpus. It pins the constant so that shrinking it back is a deliberate
    act with this measurement in front of whoever does it.
    """

    assert MAX_CHUNK_TOKENS >= 192


def test_a_model_without_a_tokenizer_still_chunks() -> None:
    """
    EMBEDDING_MODEL is operator-configurable and legitimately holds names
    with no Hugging Face tokenizer - whatever the mock provider happens to be
    pointed at. Chunking has to keep working there.
    """

    assert _load_tokenizer(_NO_TOKENIZER) is None

    spans = chunk_text("Some ordinary text about the clinic.", model=_NO_TOKENIZER)

    assert len(spans) == 1


def test_the_real_tokenizer_is_used_when_the_model_has_one() -> None:
    """
    The point of the whole change: chunks are measured by the tokenizer that
    will actually consume them, so nothing exceeds the model's own limit and
    gets silently truncated at embedding time.
    """

    model = "BAAI/bge-base-en-v1.5"
    tokenizer = _load_tokenizer(model)

    if tokenizer is None:
        pytest.skip("bge tokenizer unavailable (no transformers, or offline)")

    text = "\n\n".join(
        f"Section {i}. " + "The clinic opens at nine and closes at five. " * 12
        for i in range(40)
    )

    spans = chunk_text(text, model=model)

    assert len(spans) > 1
    for span in spans:
        # What the model really receives, special tokens included.
        assert len(tokenizer.encode(span.text)) <= 512
    _assert_offsets_match(text, spans)


def test_text_repeated_across_pages_is_indexed_once() -> None:
    """
    Site furniture, detected by repetition rather than by markup.

    `_NON_CONTENT_TAGS` already strips <nav>, <header> and <footer>, which
    covers a semantically-marked-up site - but a documentation site that
    renders its sidebar in plain <div>s defeats tag-based removal, and no
    list of selectors generalises to the next site. Measured on a 300-page
    crawl: 2,675 of 13,261 chunks were exact duplicates, a fifth of the
    index, and one retrieval returned the same chunk twice inside a top-5.
    """

    sidebar = ChunkSpan(text="Docs Guides API Reference Settings", char_start=0, char_end=34)
    spans = [
        ("https://example.com/a", sidebar),
        ("https://example.com/a", ChunkSpan(text="How billing works.", char_start=34, char_end=52)),
        ("https://example.com/b", sidebar),
        ("https://example.com/b", ChunkSpan(text="How limits work.", char_start=34, char_end=50)),
        ("https://example.com/c", sidebar),
    ]

    kept = drop_repeated_spans(spans)

    assert [span.text for _page, span in kept] == [
        "Docs Guides API Reference Settings",
        "How billing works.",
        "How limits work.",
    ]
    # The first page to carry it keeps it, so the text is still retrievable.
    assert kept[0][0] == "https://example.com/a"


def test_near_duplicates_are_left_alone() -> None:
    """
    Exact matching only. Two pages that say almost the same thing may still
    differ in the part that answers the question, and a similarity threshold
    would be guessing which. Dropping an exact repeat cannot lose anything;
    dropping a near-repeat can.
    """

    spans = [
        ("a", ChunkSpan(text="The plan costs 649 rupees.", char_start=0, char_end=26)),
        ("b", ChunkSpan(text="The plan costs 649 rupees a month.", char_start=0, char_end=34)),
    ]

    assert len(drop_repeated_spans(spans)) == 2


def test_deduping_preserves_each_span_s_own_offsets() -> None:
    """
    Citation has to keep working: a surviving span still points into its own
    page at its own offsets.
    """

    spans = [
        ("a", ChunkSpan(text="shared", char_start=10, char_end=16)),
        ("b", ChunkSpan(text="unique", char_start=99, char_end=105)),
    ]

    kept = drop_repeated_spans(spans)

    assert kept[1][1].char_start == 99
    assert kept[1][1].char_end == 105
