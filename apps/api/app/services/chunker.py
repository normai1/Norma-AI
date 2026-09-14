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
# special tokens the tokenizer adds, so 510 is the real content ceiling. This
# sits far below it, and the distance is the point.
#
# It was 400, which fits the model's window comfortably and was still much too
# large. Two measured consequences on a real knowledge base of 4,185 chunks at
# a median of 1,743 characters:
#
# - Retrieval could not discriminate. Every score across a whole call landed
#   between 0.62 and 0.73, barely clear of the relevance floor, because a
#   chunk that long covers several topics and its single embedding is the
#   average of all of them. Everything matches everything, weakly.
#
# - Only two chunks fit the context builder's 4,000-character budget, so
#   ranking mistakes were fatal rather than survivable. On one turn the FAQ
#   that directly answered a pricing question was retrieved at 0.626 and then
#   dropped, because two longer website chunks scored 0.02 higher and spent
#   the whole budget. The model was handed seat pricing for a question about
#   a different plan, and answered from it.
#
# The reply to that was 128 tokens, and it went too far in the other
# direction. Measured on the same corpus, re-chunked to 128 and re-embedded
# (10,141 chunks, median 579 characters), against eight questions the site
# answers and five it does not:
#
#     best-chunk score, answerable:    0.642 - 0.824
#     best-chunk score, unanswerable:  0.521 - 0.632
#
# The floor that decides whether the model is given anything at all sits at
# 0.62. An unanswerable question scored 0.632 - above it - so the model was
# handed chunks for a question the knowledge does not cover, which is
# exactly the failure retrieval_min_score exists to prevent. The separation
# between "we know this" and "we do not" had fallen from 0.091 to 0.010.
#
# Short chunks are the reason. A 128-token fragment carries too little to be
# about anything in particular, so it sits near everything weakly - and at
# 10,000 of them, some fragment is close enough to any question asked. The
# ceiling did not move; the noise floor came up to meet it.
#
# 256 tokens - roughly 1,150 characters - was measured the same way and is
# where both failures are avoided:
#
#     best-chunk score, answerable:    0.607 - 0.840
#     best-chunk score, unanswerable:  0.494 - 0.600   (all below the floor)
#
# And the measure that actually matters, which no score can show: for five
# questions whose answers are known to be in the corpus, the chunk holding
# the answer reached the model all five times, at 0.682 to 0.901, inside the
# context builder's 4,000-character budget with three to five chunks landing.
#
# The one answerable question that now falls below the floor is "what plans
# do you offer" at 0.607, whose best chunk was about spend alerting - so it
# was never going to be answered from that chunk anyway, and a refusal is
# the right outcome. That asymmetry is deliberate and is the same one
# retrieval_min_score is set by: refusing something known is safe and
# annoying, inventing something unknown is the failure.
MAX_CHUNK_TOKENS = 256

# Still roughly 15%, scaled with the budget above. With no overlap at all -
# which is what this used to do - a fact that straddles a boundary is split
# across two chunks and neither retrieves well for it: the usual reason a
# knowledge base "does not contain" something a reader can point to in the
# source. The cost is more chunks, more embedding calls, and a higher chance
# of two near-identical hits inside a small top-k.
CHUNK_OVERLAP_TOKENS = 40

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


def drop_repeated_spans(
    spans_by_page: list[tuple[str, ChunkSpan]],
) -> list[tuple[str, ChunkSpan]]:
    """
    Keep the first occurrence of each distinct chunk text across a whole
    source, and drop the rest.

    Site furniture is what this removes in practice. `_NON_CONTENT_TAGS`
    already strips `<nav>`, `<header>`, `<footer>` and friends, which is
    everything a semantically-marked-up site puts its menus in - but a
    documentation site that renders its sidebar in plain `<div>`s defeats
    tag-based removal entirely, and no list of CSS selectors generalises to
    the next site. Repetition does: a block of text that appears verbatim on
    dozens of pages of one site is furniture, whatever tag it arrived in.

    Measured on a 300-page crawl of one documentation site: 2,675 of 13,261
    chunks were exact duplicates, a fifth of the index. One retrieval
    returned the *same* chunk twice inside a top-5, so the model saw four
    distinct passages where it should have seen five.

    Exact matching only, and only the text. That makes this provably safe in
    a way a similarity threshold would not be: indexing one string twice can
    waste a retrieval slot but can never fill one better, so removing the
    second copy cannot lose information. Near-duplicates are left alone -
    two pages that say almost the same thing may still differ in the part
    that answers the question.

    The spans that survive keep their own char offsets into their own page,
    so citation is unaffected.
    """

    seen: set[str] = set()
    kept: list[tuple[str, ChunkSpan]] = []

    for page, span in spans_by_page:
        if span.text in seen:
            continue

        seen.add(span.text)
        kept.append((page, span))

    return kept
