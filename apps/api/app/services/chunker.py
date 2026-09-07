"""
Pure text-chunking logic: no database, no knowledge of where the text came
from. Splits normalized text into spans bounded by max_chars using
LangChain's RecursiveCharacterTextSplitter (paragraph -> line -> word ->
character, in that priority order), each carrying its char_start/char_end
offset in the original text for citation. text[char_start:char_end] always
equals the span's text.
"""

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

MAX_CHUNK_CHARS = 1500


@dataclass(frozen=True)
class ChunkSpan:
    text: str
    char_start: int
    char_end: int


def chunk_text(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[ChunkSpan]:
    """
    Splits normalized text into spans up to max_chars via
    RecursiveCharacterTextSplitter, preferring to break on paragraph, then
    line, then word boundaries before falling back to a raw character split
    for a single unbroken run longer than max_chars. Blank/empty text
    returns an empty list.
    """

    if not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chars,
        chunk_overlap=0,
        add_start_index=True,
    )

    return [
        ChunkSpan(
            document.page_content,
            document.metadata["start_index"],
            document.metadata["start_index"] + len(document.page_content),
        )
        for document in splitter.create_documents([text])
    ]
