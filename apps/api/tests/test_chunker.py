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
    _load_tokenizer,
    chunk_text,
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


def test_a_model_without_a_tokenizer_still_chunks() -> None:
    """
    EMBEDDING_MODEL is operator-configurable and legitimately holds names with
    no Hugging Face tokenizer - OpenAI's, or whatever the mock provider is
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
