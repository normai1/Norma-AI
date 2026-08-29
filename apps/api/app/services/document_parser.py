"""
Pure document-to-text parsing: no database, no I/O beyond the bytes already
in hand (StorageProvider is the caller's job). One function per supported
extension, all funneled through parse_document.
"""

import io

import docx
import pypdf


class DocumentParseError(Exception):
    """
    The bytes for a 'file'-type KnowledgeSource could not be turned into
    usable text - an unsupported extension, a corrupted file, or a file with
    no extractable text (most often a scanned/image-only PDF).
    """


def _parse_txt_or_md(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def _parse_pdf(content: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))

        if reader.is_encrypted:
            raise DocumentParseError("PDF is password-protected")

        pages_text = [page.extract_text() or "" for page in reader.pages]
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("Could not read PDF content") from exc

    text = "\n\n".join(page.strip() for page in pages_text if page.strip())

    if not text:
        raise DocumentParseError("No extractable text found in PDF")

    return text


def _parse_docx(content: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(content))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("Could not read DOCX content") from exc

    text = "\n\n".join(paragraphs)

    if not text:
        raise DocumentParseError("No extractable text found in DOCX")

    return text


_PARSERS = {
    ".txt": _parse_txt_or_md,
    ".md": _parse_txt_or_md,
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
}


def parse_document(content: bytes, extension: str) -> str:
    """
    Parse a file's raw bytes into normalized plain text. Raises
    DocumentParseError for an unsupported extension or unparseable content.
    An empty/whitespace-only .txt or .md is not an error - it normalizes to
    empty text, matching a genuinely empty document. A .pdf/.docx with no
    extractable text is an error - for those formats it almost always means
    a scanned page image or a corrupted file, not an intentionally empty one.
    """

    parser = _PARSERS.get(extension.lower())

    if parser is None:
        raise DocumentParseError(f"Unsupported extension: {extension}")

    return parser(content)
