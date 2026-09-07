import io

import docx
import pytest
from pypdf import PdfWriter

from app.services.document_parser import DocumentParseError, parse_document


def _build_blank_pdf_bytes() -> bytes:
    """
    A real, validly-structured PDF with a page but no text content, so the
    "no extractable text" failure path is exercised with a genuinely blank
    page rather than a fixture claiming to hold text it doesn't.
    """

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)

    buffer = io.BytesIO()
    writer.write(buffer)

    return buffer.getvalue()


def _build_text_pdf_bytes(text: str) -> bytes:
    """
    A minimal PDF whose single page carries one real text-drawing content
    stream, assembled here by hand: pypdf reads and rewrites PDFs but cannot
    draw text into one, and a fixture is not worth taking on a whole
    PDF-authoring dependency for. Offsets are computed rather than
    hard-coded so the cross-reference table stays correct whatever the text
    length is.
    """

    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /F1 12 Tf 20 100 Td ({escaped}) Tj ET".encode()

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []

    for number, obj in enumerate(objects, start=1):
        offsets.append(len(header) + len(body))
        body += b"%d 0 obj\n" % number + obj + b"\nendobj\n"

    xref_offset = len(header) + len(body)
    xref = b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)

    for offset in offsets:
        xref += b"%010d 00000 n \n" % offset

    trailer = b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_offset,
    )

    return header + body + xref + trailer


def _build_encrypted_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="user123", owner_password="owner")

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
    text = parse_document(_build_text_pdf_bytes("Hello knowledge base"), ".pdf")

    assert "Hello knowledge base" in text


def test_pdf_with_no_extractable_text_raises() -> None:
    blank_pdf = _build_blank_pdf_bytes()

    with pytest.raises(DocumentParseError):
        parse_document(blank_pdf, ".pdf")


def test_corrupted_pdf_raises() -> None:
    with pytest.raises(DocumentParseError):
        parse_document(b"not a real pdf at all", ".pdf")


def test_password_protected_pdf_raises() -> None:
    encrypted_pdf = _build_encrypted_pdf_bytes()

    with pytest.raises(DocumentParseError):
        parse_document(encrypted_pdf, ".pdf")


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
