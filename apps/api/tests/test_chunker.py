from app.services.chunker import chunk_text


def _assert_offsets_match(text: str, spans: list) -> None:
    for span in spans:
        assert text[span.char_start : span.char_end] == span.text


def test_empty_text_returns_no_spans() -> None:
    assert chunk_text("") == []


def test_whitespace_only_text_returns_no_spans() -> None:
    assert chunk_text("   \n\n  \t \n") == []


def test_short_text_returns_one_span() -> None:
    text = "Our hours are 9am to 5pm, Monday through Friday."

    spans = chunk_text(text, max_chars=1500)

    assert len(spans) == 1
    assert spans[0].text == text
    _assert_offsets_match(text, spans)


def test_multiple_paragraphs_are_packed_into_bounded_spans() -> None:
    paragraph = "Paragraph text repeated to take up real space. " * 3
    text = "\n\n".join([paragraph] * 5)

    spans = chunk_text(text, max_chars=120)

    assert len(spans) > 1
    for span in spans:
        assert len(span.text) <= 120
    _assert_offsets_match(text, spans)
    # Packing must not drop or reorder any paragraph's content.
    assert "".join(span.text for span in spans).count("Paragraph text") == 15


def test_short_paragraphs_pack_into_a_single_span_when_they_fit() -> None:
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."

    spans = chunk_text(text, max_chars=1500)

    assert len(spans) == 1
    _assert_offsets_match(text, spans)


def test_oversized_single_paragraph_falls_back_to_whitespace_split() -> None:
    words = [f"word{i}" for i in range(200)]
    text = " ".join(words)

    spans = chunk_text(text, max_chars=50)

    assert len(spans) > 1
    for span in spans:
        assert len(span.text) <= 50
        # No word is ever torn in half.
        assert all(part.startswith("word") for part in span.text.split())
    _assert_offsets_match(text, spans)
    assert " ".join(span.text for span in spans) == text


def test_a_single_word_longer_than_max_chars_becomes_its_own_span() -> None:
    text = "x" * 5000

    spans = chunk_text(text, max_chars=1500)

    assert len(spans) == 1
    assert spans[0].text == text
    _assert_offsets_match(text, spans)
