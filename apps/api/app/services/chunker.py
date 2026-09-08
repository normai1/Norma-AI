"""
Pure text-chunking logic: no database, no knowledge of where the text came
from. Splits normalized text into spans bounded by a token budget using
LangChain's RecursiveCharacterTextSplitter (paragraph -> line -> word ->
character, in that priority order), each carrying its char_start/char_end
offset in the original text for citation. text[char_start:char_end] always
equals the span's text.

The budget is counted with the **embedding model's own tokenizer**, handed to
the splitter as its length_function. That is the only ruler that measures the
thing that actually matters: the model silently truncates anything past its
input limit, so a chunk over the limit is stored and retrievable but embedded
from only its opening, and nothing anywhere reports it.

Counting characters cannot see that limit at all - 1500 characters of dense
prose and 1500 of a sparse table are very different amounts of input. Counting
with a general-purpose tokenizer (tiktoken's cl100k_base) is closer but still
the wrong ruler: it is not the tokenizer the model uses, so the budget needs an
arbitrary safety margin against a limit it cannot measure exactly.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings

# BAAI/bge-base-en-v1.5 and its siblings accept 512 tokens including the two
# special tokens the tokenizer adds, so 510 is the real content ceiling. The
# budget sits below it rather than at it: a chunk that exactly fills the
# window leaves the model no room, and smaller chunks retrieve more precisely
# anyway, since a single embedding has to represent everything in them.
MAX_CHUNK_TOKENS = 400

# Roughly 15%. With no overlap at all - which is what this used to do - a fact
# that straddles a boundary is split across two chunks and neither retrieves
# well for it: the usual reason a knowledge base "does not contain" something
# a reader can point to in the source. The cost is more chunks, more embedding
# calls, and a higher chance of two near-identical hits inside a small top-k.
CHUNK_OVERLAP_TOKENS = 60

# Characters per token when no real tokenizer is available - see
# _length_function. Deliberately pessimistic (English prose runs nearer 4)
# so the fallback under-fills the window rather than overflowing it.
_FALLBACK_CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class ChunkSpan:
    text: str
    char_start: int
    char_end: int


@lru_cache(maxsize=4)
def _load_tokenizer(model: str):
    """
    The configured embedding model's tokenizer, or None if it has none to
    load.

    Cached because loading parses a vocabulary from disk, and chunking runs
    once per document rather than once per process.

    A tokenizer is a vocabulary file, not model weights - it does not repeat
    the in-process model hosting CLAUDE.md section 6.4 records as having
    failed here, on both load time and a crash inside the model's own code.

    Returns None rather than raising for a model that has no Hugging Face
    tokenizer at all: EMBEDDING_MODEL is operator-configurable and legitimately
    holds OpenAI names like "text-embedding-3-small", and the mock provider
    uses whatever is configured without caring. Chunking must still work
    there - see _length_function for what happens instead.
    """

    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(model)
    except Exception:
        return None


def _length_function(model: str) -> Callable[[str], int]:
    """
    How to measure a chunk against the budget.

    Falls back to a pessimistic characters-per-token estimate when the model
    has no loadable tokenizer, so an OpenAI or mock embedding configuration
    still chunks sensibly. The estimate under-fills the window rather than
    overflowing it, because overflowing is the failure that is silent.
    """

    tokenizer = _load_tokenizer(model)

    if tokenizer is None:
        return lambda text: len(text) // _FALLBACK_CHARS_PER_TOKEN + 1

    # add_special_tokens=False counts the content only; the budget above
    # already reserves room for the pair the model adds.
    return lambda text: len(tokenizer.encode(text, add_special_tokens=False))


def chunk_text(
    text: str,
    *,
    max_tokens: int = MAX_CHUNK_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
    model: str | None = None,
) -> list[ChunkSpan]:
    """
    Splits normalized text into spans of at most max_tokens, overlapping by
    overlap_tokens, preferring to break on paragraph, then line, then word
    boundaries before falling back to a raw character split for a single
    unbroken run longer than the budget. Blank/empty text returns an empty
    list.

    Still a *character* splitter measured by a token counter: it breaks on the
    same text boundaries as before, and the offsets stay character offsets
    into the original, so the char_start/char_end contract is unchanged. Only
    the size limit is counted in tokens.
    """

    if not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_tokens,
        chunk_overlap=overlap_tokens,
        length_function=_length_function(model or settings.embedding_model),
    )

    spans: list[ChunkSpan] = []
    # Offsets are located here rather than through the splitter's own
    # add_start_index, which cannot survive overlap: it searches forward from
    # the end of the previous chunk, and an overlapping chunk starts before
    # that point, so it reports -1 and char_end lands past the end of the
    # text. Silent, and only visible if something actually slices the original
    # with the result - which the citation path does.
    #
    # Searching from one character past the previous chunk's own start keeps
    # the scan moving forward while still allowing the next chunk to begin
    # inside this one.
    search_from = 0

    for document in splitter.create_documents([text]):
        content = document.page_content
        found = text.find(content, search_from)

        if found == -1:
            # Whitespace normalization can leave a chunk that is not a
            # verbatim slice of the original; a global search is the last
            # chance to anchor it honestly.
            found = text.find(content)

        if found == -1:
            # No offset can satisfy text[char_start:char_end] == content, so
            # the span is dropped rather than stored with offsets that lie.
            continue

        spans.append(ChunkSpan(content, found, found + len(content)))
        search_from = found + 1

    return spans
