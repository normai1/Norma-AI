"""
Turns a knowledge source's ingested text into candidate customer-facing FAQ
entries via the configured LLM provider, then saves each one as a real
FaqEntry (embedded and chunked) under a per-assistant "Generated FAQs"
container. Runs once, immediately after a file/website source's own
parse+chunk+embed first succeeds - not on a later reprocess/recrawl, since
FaqEntry has no way to track "which generated entry came from which run,"
and regenerating on every retry would pile up duplicate entries rather than
replacing them. A generation failure never fails the source itself: the
source's own processing already completed successfully, and a flaky
generation call is a missed enhancement, not a broken upload (CLAUDE.md
section 26's "a call that was handled well but whose summary never arrived
is a support ticket" reasoning applied to this background step).
"""

import asyncio
import json
import logging
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_source import KnowledgeSource
from app.providers.embedding import EmbeddingProvider
from app.providers.llm import LLMProvider, LLMProviderError
from app.repositories import knowledge_source as knowledge_source_repo
from app.services import faq_entry as faq_entry_service

logger = logging.getLogger(__name__)

GENERATED_FAQ_SOURCE_NAME = "Generated FAQs"

# How many Q&A pairs are asked for from ONE window of the source. The
# document is covered window by window, so this is no longer the ceiling on
# a whole document - see MAX_GENERATED_ENTRIES for that.
MAX_ENTRIES_PER_WINDOW = 8

# Ceiling across the whole source, so a very large document produces a long
# FAQ list rather than an unusable one.
MAX_GENERATED_ENTRIES = 150

# How much text goes into one generation call. This used to be applied to the
# document as a whole - text[:MAX_SOURCE_TEXT_CHARS] - which meant a 50-page
# PDF of 143,000 characters had FAQs written from its first 12,000 and the
# other 92% was never read. It produced 8 entries for a document that should
# have yielded dozens, and nothing said so.
MAX_SOURCE_TEXT_CHARS = 12_000

# How many windows one source may consume, bounding cost and time for a very
# large document rather than letting them scale without limit. At 12,000
# characters each this covers roughly 240,000 - around 85 pages of ordinary
# prose - after which the remainder is deliberately not read.
MAX_SOURCE_WINDOWS = 20

# How many already-written questions a later window is shown, so it can avoid
# repeating them. Bounded so the reminder cannot crowd out the document text
# it is meant to accompany.
_RECENT_QUESTIONS_SHOWN = 40

# Two questions sharing this proportion of their meaningful words are treated
# as the same question. Measured on real generated output rather than picked:
# obvious rewordings scored 0.67, 0.33 and 0.14, while genuinely distinct
# questions topped out at 0.18 - so 0.5 removes the worst repeats with room to
# spare and cannot reach the distinct ones.
#
# It deliberately does not catch a paraphrase built from different words
# ("Is there a fee for posting a job?" against "What does it cost to post a
# job?"), and nothing cheap does: embedding similarity was measured too, and
# ranked a true duplicate at 0.879 below unrelated pairs at 0.876 and 0.851.
# The avoid-list in the prompt is what prevents most of those; this is the
# backstop for repeats inside a single window, which the avoid-list cannot see.
_DUPLICATE_WORD_OVERLAP = 0.5

# Carry no meaning for telling two questions apart.
_QUESTION_STOP_WORDS = frozenset(
    {
        "what", "is", "are", "the", "a", "an", "of", "for", "to", "in", "on",
        "how", "does", "do", "can", "it", "and", "with", "you", "your", "be",
        "by", "there", "any", "will", "when", "which", "that", "this",
    }
)

# A rate-limited window is retried rather than lost. The provider answers a
# burst of windows with 429 and recovers a moment later, so the difference
# between retrying and not is most of the document's questions.
_WINDOW_MAX_ATTEMPTS = 4
_WINDOW_RETRY_BACKOFF_SECONDS = 2.0

_SYSTEM_PROMPT = (
    "You write concise customer-facing FAQ entries for a business phone "
    "assistant. You are given raw text from one of the business's own "
    "documents or web pages. Produce realistic questions a caller might "
    "ask this business, with answers grounded ONLY in the given text - "
    "never invent a fact, price, hours, or policy that is not present in "
    "it. "
    # Without a target the model returns two or three pairs regardless of
    # how much the text covers - measured at ~1.8 per window against a real
    # document, which is what left a 50-page PDF with a handful of entries
    # even once the whole of it was being read.
    f"Cover the material given to you: produce up to {MAX_ENTRIES_PER_WINDOW} "
    "distinct entries, drawing on different parts of the text rather than "
    "several questions about the same detail. Produce fewer only if the "
    "text genuinely does not support that many. "
    "Respond with nothing but a JSON array of objects, each with a "
    '"question" and an "answer" string key. If the text has no genuinely '
    "useful FAQ content, respond with an empty array []."
)


def _strip_code_fence(raw_response: str) -> str:
    """
    Defensive normalization: strips a markdown code fence around the
    response if the model added one despite being told not to, so a purely
    cosmetic wrapper never turns a valid response into a parse failure.
    """

    text = raw_response.strip()

    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()

    return text


def _extract_pairs(raw_response: str) -> list[tuple[str, str]]:
    """
    Parses the model's JSON response into (question, answer) pairs,
    dropping any malformed entry rather than failing the whole batch -
    CLAUDE.md's "malformed model output" resilience requirement applied
    here. A completely unparseable response yields no pairs, not an
    exception.
    """

    try:
        data = json.loads(_strip_code_fence(raw_response))
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    pairs: list[tuple[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        question = item.get("question")
        answer = item.get("answer")

        if (
            isinstance(question, str)
            and isinstance(answer, str)
            and question.strip()
            and answer.strip()
        ):
            pairs.append((question.strip(), answer.strip()))

    return pairs[:MAX_ENTRIES_PER_WINDOW]


async def _get_or_create_generated_faq_source(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    owner_user_id: uuid.UUID | None,
) -> KnowledgeSource:
    """
    One manual_faq-type container per assistant holds every AI-generated
    FAQ entry, reused across every file/website source's own generation run
    rather than creating a new container each time.
    """

    existing = await knowledge_source_repo.find_by_assistant_type_and_name(
        db,
        assistant_id=assistant_id,
        type=knowledge_source_repo.MANUAL_FAQ_TYPE,
        name=GENERATED_FAQ_SOURCE_NAME,
    )

    if existing is not None:
        return existing

    knowledge_source = KnowledgeSource(
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        type=knowledge_source_repo.MANUAL_FAQ_TYPE,
        owner_user_id=owner_user_id,
        name=GENERATED_FAQ_SOURCE_NAME,
        # Ready as soon as it exists - a container has nothing to process.
        # Left at the 'pending' default it reported itself, forever, as a
        # knowledge base still being built.
        status=knowledge_source_repo.COMPLETED_STATUS,
    )
    db.add(knowledge_source)
    await db.flush()

    return knowledge_source


def _windows(text: str) -> list[str]:
    """
    Split a source into generation-sized windows, on paragraph boundaries
    where possible so a window rarely starts or ends mid-sentence.

    Bounded by MAX_SOURCE_WINDOWS: a document past that is covered up to the
    limit and no further, which is a deliberate ceiling on cost rather than
    an accident of slicing.
    """

    remaining = text.strip()
    windows: list[str] = []

    while remaining and len(windows) < MAX_SOURCE_WINDOWS:
        if len(remaining) <= MAX_SOURCE_TEXT_CHARS:
            windows.append(remaining)
            break

        head = remaining[:MAX_SOURCE_TEXT_CHARS]
        # Prefer a paragraph break, then any line break, then wherever the
        # limit falls - the same priority order the chunker splits on.
        split_at = head.rfind("\n\n")

        if split_at < MAX_SOURCE_TEXT_CHARS // 2:
            split_at = head.rfind("\n")

        if split_at < MAX_SOURCE_TEXT_CHARS // 2:
            split_at = MAX_SOURCE_TEXT_CHARS

        windows.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    return [window for window in windows if window]


def _normalized_question(question: str) -> str:
    """A question's identity for deduplication: case and punctuation aside."""

    return re.sub(r"[^a-z0-9 ]", "", question.casefold()).strip()


def _meaningful_words(question: str) -> set[str]:
    return {
        word
        for word in _normalized_question(question).split()
        if word not in _QUESTION_STOP_WORDS
    }


def _is_reworded(question: str, kept: list[set[str]]) -> bool:
    """
    Whether this question is one already kept, in different words.
    """

    words = _meaningful_words(question)

    if not words:
        return False

    for existing in kept:
        union = words | existing

        if union and len(words & existing) / len(union) >= _DUPLICATE_WORD_OVERLAP:
            return True

    return False


def _deduplicate(
    pair_groups: list[list[tuple[str, str]]],
) -> list[tuple[str, str]]:
    """
    Merge every window's pairs, dropping repeats.

    Windows overlap in subject matter even when they do not overlap in text -
    a document that mentions opening hours in three places will be asked
    about them three times - and a FAQ list with the same question answered
    repeatedly is worse than a shorter one.
    """

    seen: set[str] = set()
    kept_words: list[set[str]] = []
    merged: list[tuple[str, str]] = []

    for pairs in pair_groups:
        for question, answer in pairs:
            key = _normalized_question(question)

            if not key or key in seen or _is_reworded(question, kept_words):
                continue

            seen.add(key)
            kept_words.append(_meaningful_words(question))
            merged.append((question, answer))

            if len(merged) >= MAX_GENERATED_ENTRIES:
                return merged

    return merged


def _avoid_clause(already_asked: list[str]) -> str:
    """
    The instruction that keeps a later window off ground an earlier one
    already covered.

    Windows are slices of one document, and a business repeats itself across
    its own pages - pricing, turnaround and data handling get mentioned in
    several places. Generated independently, each window asks about them
    again, and the result is a FAQ list where a third of the questions are
    variations of each other.

    Detecting that afterwards does not work: measured on a real site, a
    genuine duplicate pair scored 0.879 cosine while unrelated pairs scored
    0.876 and 0.851, so no threshold separates them. Not generating the
    duplicate is the only reliable option.
    """

    if not already_asked:
        return ""

    recent = already_asked[-_RECENT_QUESTIONS_SHOWN:]
    listed = "\n".join(f"- {question}" for question in recent)

    return (
        "\n\nQuestions already written for this business, from earlier parts "
        "of the same document. Do not ask any of these again, and do not ask "
        "a reworded version of one - cover something they do not:\n"
        f"{listed}"
    )


async def _generate_for_window(
    llm_provider: LLMProvider,
    window: str,
    *,
    already_asked: list[str],
    knowledge_source_id: uuid.UUID,
) -> list[tuple[str, str]]:
    """
    One window's pairs, or none if the provider fails for it.

    A window failing is not allowed to lose the rest: a large document is
    many calls, and one of them erroring should cost that window's questions,
    not the whole set.
    """

    prompt = window + _avoid_clause(already_asked)

    for attempt in range(1, _WINDOW_MAX_ATTEMPTS + 1):
        try:
            raw_response = await llm_provider.generate(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
        except LLMProviderError as exc:
            if attempt == _WINDOW_MAX_ATTEMPTS:
                logger.warning(
                    "FAQ generation gave up on one window of knowledge "
                    "source %s after %d attempts: %s",
                    knowledge_source_id,
                    attempt,
                    type(exc).__name__,
                )

                return []

            await asyncio.sleep(_WINDOW_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        else:
            return _extract_pairs(raw_response)

    return []


async def generate_faq_entries_for_source(
    db: AsyncSession,
    llm_provider: LLMProvider,
    embedding_provider: EmbeddingProvider,
    *,
    knowledge_source: KnowledgeSource,
    text: str,
) -> None:
    """
    Best-effort: generates candidate FAQ entries from a just-processed
    file/website source's text and saves each as a real FaqEntry. Any
    failure (provider error, malformed output, a single entry's embedding
    call failing) is logged and swallowed - the calling source's own status
    is never affected.

    The source is covered window by window rather than by its opening alone,
    so the number of entries scales with how much the document actually says.
    Windows run concurrently, bounded, because this still runs inside the
    upload request.
    """

    if not text.strip() or knowledge_source.assistant_id is None:
        return

    windows = _windows(text)

    # Sequential, not concurrent, for two reasons that happen to agree. A
    # window can only avoid repeating earlier questions if it is told what
    # they were, which requires the earlier ones to have finished. And
    # concurrency is what provoked the provider into rate-limiting most of a
    # document - measured at 3 of 13 windows answered.
    pair_groups: list[list[tuple[str, str]]] = []
    already_asked: list[str] = []

    for window in windows:
        pairs_for_window = await _generate_for_window(
            llm_provider,
            window,
            already_asked=already_asked,
            knowledge_source_id=knowledge_source.id,
        )
        pair_groups.append(pairs_for_window)
        already_asked.extend(question for question, _answer in pairs_for_window)

    pairs = _deduplicate(pair_groups)

    productive = sum(1 for group in pair_groups if group)

    if productive < len(windows):
        # Silent before: a window lost to rate limiting looked exactly like a
        # window with nothing worth asking about, and the only visible symptom
        # was a short FAQ list.
        logger.warning(
            "FAQ generation for knowledge source %s: only %d of %d windows "
            "produced entries",
            knowledge_source.id,
            productive,
            len(windows),
        )

    logger.info(
        "FAQ generation for knowledge source %s: %d window(s), %d entries",
        knowledge_source.id,
        len(windows),
        len(pairs),
    )

    if not pairs:
        return

    faq_source = await _get_or_create_generated_faq_source(
        db,
        organization_id=knowledge_source.organization_id,
        workspace_id=knowledge_source.workspace_id,
        assistant_id=knowledge_source.assistant_id,
        owner_user_id=knowledge_source.owner_user_id,
    )

    for question, answer in pairs:
        try:
            await faq_entry_service.create_faq_entry(
                db,
                embedding_provider,
                organization_id=knowledge_source.organization_id,
                workspace_id=knowledge_source.workspace_id,
                knowledge_source_id=faq_source.id,
                question=question,
                answer=answer,
                # The entry lives in the shared generated container, but
                # remembers the document it came from, so deleting that
                # document takes this entry with it.
                generated_from_knowledge_source_id=knowledge_source.id,
            )
        except Exception:
            logger.warning(
                "Failed to save a generated FAQ entry for knowledge "
                "source %s",
                knowledge_source.id,
                exc_info=True,
            )
