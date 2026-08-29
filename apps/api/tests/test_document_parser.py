import io

import docx
import pytest
from pypdf import PdfWriter

from app.services.document_parser import DocumentParseError, parse_document


def _build_blank_pdf_bytes() -> bytes:
    """
    A real, validly-structured PDF with a page but no text content - pypdf
    can't draw text without a rendering library, so the "no extractable
    text" failure path is exercised with a genuinely blank page rather than
    a fixture claiming to hold text it doesn't.
    """

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)

    buffer = io.BytesIO()
    writer.write(buffer)

    return buffer.getvalue()


def _build_docx_bytes(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)

    buffer = io.BytesIO()
    document.save(buffer)

    return buffer.getvalue()


# A minimal, hand-built single-page PDF with a real embedded text stream
# ("Hello knowledge base") - built once so the happy-path test does not
# depend on a rendering library to produce extractable text.
_MINIMAL_TEXT_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /Resources "
    b"<< /Font << /F1 4 0 R >> >> /MediaBox [0 0 300 144] "
    b"/Contents 5 0 R >> endobj\n"
    b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> "
    b"endobj\n"
    b"5 0 obj << /Length 58 >>\n"
    b"stream\n"
    b"BT /F1 18 Tf 10 100 Td (Hello knowledge base) Tj ET\n"
    b"endstream\n"
    b"endobj\n"
    b"xref\n"
    b"0 6\n"
    b"trailer << /Root 1 0 R /Size 6 >>\n"
    b"startxref\n"
    b"0\n"
    b"%%EOF"
)


def test_parses_txt_content() -> None:
    assert parse_document(b"Our hours are 9am to 5pm.", ".txt") == (
        "Our hours are 9am to 5pm."
    )


def test_parses_md_content() -> None:
    assert (
        parse_document(b"# Heading\n\nBody text.", ".md") == "# Heading\n\nBody text."
    )


def test_txt_extension_is_case_insensitive() -> None:
    assert parse_document(b"hi", ".TXT") == "hi"


def test_unsupported_extension_raises() -> None:
    with pytest.raises(DocumentParseError):
        parse_document(b"whatever", ".exe")


def test_parses_pdf_with_real_text_stream() -> None:
    text = parse_document(_MINIMAL_TEXT_PDF, ".pdf")

    assert "Hello knowledge base" in text


def test_pdf_with_no_extractable_text_raises() -> None:
    blank_pdf = _build_blank_pdf_bytes()

    with pytest.raises(DocumentParseError):
        parse_document(blank_pdf, ".pdf")


def test_corrupted_pdf_raises() -> None:
    with pytest.raises(DocumentParseError):
        parse_document(b"not a real pdf at all", ".pdf")


def test_parses_docx_paragraphs() -> None:
    content = _build_docx_bytes(["First paragraph.", "Second paragraph."])

    text = parse_document(content, ".docx")

    assert "First paragraph." in text
    assert "Second paragraph." in text


def test_docx_with_no_paragraphs_raises() -> None:
    content = _build_docx_bytes([])

    with pytest.raises(DocumentParseError):
        parse_document(content, ".docx")


def test_corrupted_docx_raises() -> None:
    with pytest.raises(DocumentParseError):
        parse_document(b"not a real docx at all", ".docx")
