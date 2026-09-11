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
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge_source import KnowledgeSource
from app.providers.embedding import EmbeddingProvider
from app.providers.llm import LLMProvider, LLMProviderError, LLMRateLimited
from app.repositories import knowledge_source as knowledge_source_repo
from app.services import faq_entry as faq_entry_service
from app.services.token_rate_limiter import (
    CHARS_PER_TOKEN as _CHARS_PER_PROMPT_TOKEN,
)
from app.services.token_rate_limiter import (
    TokenRateLimiter,
    estimate_tokens,
)

logger = logging.getLogger(__name__)

GENERATED_FAQ_SOURCE_NAME = "Generated FAQs"


@dataclass(frozen=True)
class GenerationOutcome:
    """
    What one run of generation managed, so the caller can say whether the
    source is really finished.

    It used to return nothing, and the source was marked completed the
    moment its text was embedded - before a single question had been
    written. An operator uploading a 50-page PDF saw "completed" and no
    FAQs for the fourteen minutes generation actually takes, with no way to
    tell that from a document that produced none.
    """

    windows: int
    windows_covered: int
    entries: int
    # Why it stopped short, or None if it read the whole document. Shown to
    # the operator, so it says what they can do about it.
    stopped_reason: str | None = None

    @property
    def covered_everything(self) -> bool:
        return self.stopped_reason is None


class LLMQuotaExhausted(Exception):
    """
    The provider's allowance is spent for long enough that waiting it out is
    not worth doing. Stops the whole document, not one window: every
    remaining window would only wait out the same allowance in turn.
    """

    def __init__(
        self,
        *,
        exhausted_window: str | None,
        retry_after_seconds: float | None,
    ) -> None:
        super().__init__(
            f"provider allowance exhausted (per {exhausted_window or 'unknown'})"
        )
        self.exhausted_window = exhausted_window
        self.retry_after_seconds = retry_after_seconds

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
# large document rather than letting them scale without limit.
#
# Raised from 20 when the window stopped being a fixed 12,000 characters:
# window_chars_for_budget sizes it to divide the provider's minute, and
# against an 8,000-per-minute allowance that is about 9,800 characters, so
# 20 windows would have stopped a 50-page PDF three-quarters of the way
# through - the "only generates FAQs on the first few pages" complaint,
# reintroduced by the fix for it. 40 covers roughly 390,000 characters at
# that size, comfortably past any single upload the product accepts.
MAX_SOURCE_WINDOWS = 40

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
# Four attempts was chosen against provider *errors*, which are unlikely to
# clear quickly. A rate limit is the opposite: it clears at a time the
# provider names, so an attempt that waits for it is nearly certain to
# succeed. Four quick tries against a minute-long window meant a window
# was abandoned while the answer was simply "wait".
_WINDOW_MAX_ATTEMPTS = 4
_WINDOW_MAX_RATE_LIMITED_ATTEMPTS = 8

# The longest this will wait out a rate limit before deciding the allowance
# is not coming back soon enough to be worth holding a background task open
# for. Two minutes covers any per-minute bucket with room to spare.
#
# Beyond it, waiting is not patience but denial. Seen live: a tokens-per-day
# allowance of 200,000 with 199,529 used, and generation settling in to
# "waiting 1169.0s as the provider asked" - per window, on a ten-window
# document, for a quota that would not refill until the next day. The
# operator saw an upload that completed and no FAQs, with nothing anywhere
# saying why.
_MAX_RATE_LIMIT_WAIT_SECONDS = 120.0
_WINDOW_RETRY_BACKOFF_SECONDS = 2.0

_SYSTEM_PROMPT = (
    "You write concise customer-facing FAQ entries for a business phone "
    "assistant. You are given raw text from one of the business's own "
    "documents or web pages. Produce realistic questions a caller might "
    "ask this business, with answers grounded ONLY in the given text - "
    "never invent a fact, price, hours, or policy that is not present in "
    "it. "
    # A crawl captures a page's marketing mockups along with its real
    # content. A staged conversation on renate.in's candidate page showed a
    # sample form filled in with "+91 1234123400" and a made-up name, and
    # that number was written into the knowledge base as the company's
    # support line - the model reported its text faithfully, which is exactly
    # the problem. Real contact details for the same business were elsewhere
    # in the same crawl.
    "Web pages contain example and placeholder content as well as real "
    "content: sample forms, screenshots of conversations, demo data, and "
    "invented names, numbers and addresses used for illustration. Never "
    "present those as the business's real details. A phone number, email or "
    "name that appears inside a filled-in sample form or a staged "
    "conversation is an illustration, not a fact - and an obviously "
    "sequential or repeated number is never a real one. If the text does not "
    "clearly contain a genuine value, do not write a question about it. "
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
        logger.warning(
            "FAQ generation could not parse a model response as JSON "
            "(%d characters) - that window contributes nothing",
            len(raw_response),
        )

        return []

    if not isinstance(data, list):
        # A window that answers with the right content in the wrong shape -
        # an object wrapping the list, say - is worth telling apart from one
        # that genuinely had nothing to ask. Both used to look like zero.
        logger.warning(
            "FAQ generation got %s where a list of pairs was expected - "
            "that window contributes nothing",
            type(data).__name__,
        )

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


# The smallest window worth sending. Every call pays the system prompt, the
# avoid-list and a completion allowance whatever its size, so slicing a
# document finer buys nothing and spends more of the very budget being
# rationed.
_MIN_SOURCE_TEXT_CHARS = 5_000


def window_chars_for_budget(budget: int) -> int:
    """
    How much text to put in one window, given a tokens-per-minute budget.

    Every call carries the same fixed overhead whatever its size - the
    system prompt, the list of questions already asked, and the allowance
    for the reply. Measured against the real prompts that is about 2,300
    tokens, most of it the avoid-list once a document is well under way. So
    a document's total cost is its own text plus that overhead once per
    window, and fewer, larger windows cost less.

    That is what decides the size, because the scarce thing is the daily
    allowance rather than the minute: 50 pages is about 166,000 characters,
    which is 14 windows and 80,000 tokens at 12,000 characters each, against
    17 windows and 87,000 at 9,800. Both take about the same wall-clock time,
    since only one window fits a minute either way.

    An earlier version sized the window to pack two into a minute and
    ignored the overhead entirely, so the windows it chose cost 5,115 tokens
    against the 4,000 they were sized for - fitting one per minute after
    all, and paying the overhead three extra times for the privilege.

    So: the largest window that still fits inside one minute, capped at
    MAX_SOURCE_TEXT_CHARS because past that a single window dilutes the
    questions written from it, and floored at _MIN_SOURCE_TEXT_CHARS because
    below that the overhead dominates what is actually being asked.
    """

    for_text = budget - _per_call_overhead_tokens()

    if for_text <= 0:
        # The budget cannot cover even an empty call. Nothing here can fix
        # that, so send the smallest useful window and let the rate limiter
        # and the provider say what they say.
        return _MIN_SOURCE_TEXT_CHARS

    chars = min(MAX_SOURCE_TEXT_CHARS, int(for_text * _CHARS_PER_PROMPT_TOKEN))

    # The estimate rounds up, so the inverse lands a token or two over.
    while (
        chars > _MIN_SOURCE_TEXT_CHARS
        and estimate_tokens("x" * chars) > for_text
    ):
        chars -= int(_CHARS_PER_PROMPT_TOKEN) + 1

    return max(_MIN_SOURCE_TEXT_CHARS, chars)


def _per_call_overhead_tokens() -> int:
    """
    What one generation call costs before any of the document is added.

    Measured rather than guessed: the system prompt is 367 tokens, a full
    avoid-list of _RECENT_QUESTIONS_SHOWN questions about 749, and the
    reply allowance 1,200. The avoid-list is sized at its largest on
    purpose - it grows as a document is worked through, and a window sized
    against the empty one would creep over budget exactly when a long
    document is halfway done.
    """

    longest_avoid_list = [
        # Representative of what generation actually writes, and long
        # enough that a real question list cannot exceed it by much.
        "What is the policy for situation number 00 at the company?"
    ] * _RECENT_QUESTIONS_SHOWN

    return (
        estimate_tokens(_SYSTEM_PROMPT)
        + estimate_tokens(_avoid_clause(longest_avoid_list))
        + _ASSUMED_COMPLETION_TOKENS
    )



def _windows(text: str, *, max_chars: int = MAX_SOURCE_TEXT_CHARS) -> list[str]:
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
        if len(remaining) <= max_chars:
            windows.append(remaining)
            break

        head = remaining[:max_chars]
        # Prefer a paragraph break, then any line break, then wherever the
        # limit falls - the same priority order the chunker splits on.
        split_at = head.rfind("\n\n")

        if split_at < max_chars // 2:
            split_at = head.rfind("\n")

        if split_at < max_chars // 2:
            split_at = max_chars

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


# What one window's reply is assumed to cost, on top of what was sent. The
# provider counts prompt and completion against the same per-minute budget,
# and only says what the completion actually cost afterwards - by which time
# the tokens are already spent. MAX_ENTRIES_PER_WINDOW question-and-answer
# pairs of ordinary length land comfortably under this.
_ASSUMED_COMPLETION_TOKENS = 1_200


def _provider_budget(llm_provider: LLMProvider):
    """
    What the provider last said about the token allowance, or None.

    Optional on the protocol, so a provider that has never heard of rate
    limits - the mock, and any future adapter - simply reports nothing and
    the configured budget stands.
    """

    reader = getattr(llm_provider, "last_token_budget", None)

    if reader is None:
        return None

    return reader()


async def _generate_for_window(
    llm_provider: LLMProvider,
    window: str,
    *,
    already_asked: list[str],
    knowledge_source_id: uuid.UUID,
    rate_limiter: TokenRateLimiter | None = None,
) -> list[tuple[str, str]]:
    """
    One window's pairs, or none if the provider fails for it.

    A window failing is not allowed to lose the rest: a large document is
    many calls, and one of them erroring should cost that window's questions,
    not the whole set.

    When a rate_limiter is given, this waits for room in the provider's
    per-minute token budget before each attempt rather than sending the call
    and being refused. Waiting costs a document that finishes later; not
    waiting cost most of a 50-page PDF being silently never read.
    """

    prompt = window + _avoid_clause(already_asked)

    cost = (
        estimate_tokens(_SYSTEM_PROMPT)
        + estimate_tokens(prompt)
        + _ASSUMED_COMPLETION_TOKENS
    )

    errors = 0
    rate_limited = 0

    while True:
        try:
            if rate_limiter is not None:
                await rate_limiter.acquire(cost)

            raw_response = await llm_provider.generate(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=prompt,
            )
        except LLMRateLimited as exc:
            rate_limited += 1
            # The provider both refused this call and said when to try
            # again. Waiting that long is the fastest recovery and the only
            # one guaranteed not to earn a second refusal - and the budget
            # it reported alongside is worth more than whatever this was
            # pacing against, since it evidently was not enough.
            if rate_limiter is not None:
                rate_limiter.adopt_provider_budget(_provider_budget(llm_provider))

            if rate_limited >= _WINDOW_MAX_RATE_LIMITED_ATTEMPTS:
                logger.warning(
                    "FAQ generation gave up on one window of knowledge "
                    "source %s after %d rate-limited attempts",
                    knowledge_source_id,
                    rate_limited,
                )

                return []

            wait = exc.retry_after_seconds

            if wait is None:
                wait = _WINDOW_RETRY_BACKOFF_SECONDS * (2 ** (rate_limited - 1))

            # A day's allowance does not come back by waiting, and neither
            # does anything else this far out. Raising it tells the caller
            # to stop the whole document rather than have every remaining
            # window discover the same thing one long sleep at a time.
            if exc.exhausted_window == "day" or wait > _MAX_RATE_LIMIT_WAIT_SECONDS:
                raise LLMQuotaExhausted(
                    exhausted_window=exc.exhausted_window,
                    retry_after_seconds=exc.retry_after_seconds,
                ) from exc

            logger.info(
                "FAQ generation rate limited on knowledge source %s - "
                "waiting %.1fs as the provider asked",
                knowledge_source_id,
                wait,
            )

            await asyncio.sleep(wait)
        except LLMProviderError as exc:
            errors += 1

            if errors >= _WINDOW_MAX_ATTEMPTS:
                logger.warning(
                    "FAQ generation gave up on one window of knowledge "
                    "source %s after %d attempts: %s",
                    knowledge_source_id,
                    errors,
                    type(exc).__name__,
                )

                return []

            await asyncio.sleep(_WINDOW_RETRY_BACKOFF_SECONDS * (2 ** (errors - 1)))
        else:
            if rate_limiter is not None:
                rate_limiter.adopt_provider_budget(_provider_budget(llm_provider))

            return _extract_pairs(raw_response)

    return []


def _stopped_reason(exhausted: "LLMQuotaExhausted | None") -> str | None:
    """
    What to tell the operator, in their terms rather than the provider's.

    They cannot act on "429" or on a token budget, but they can act on "this
    will work again tomorrow, or on a larger plan" - and, either way, on
    knowing that the document was only partly read.
    """

    if exhausted is None:
        return None

    if exhausted.exhausted_window == "day":
        return (
            "The AI provider's daily limit was reached partway through this "
            "document, so only part of it has been turned into FAQs. Retry "
            "once the limit resets."
        )

    return (
        "The AI provider stopped accepting requests partway through this "
        "document, so only part of it has been turned into FAQs. Retry to "
        "finish it."
    )


async def generate_faq_entries_for_source(
    db: AsyncSession,
    llm_provider: LLMProvider,
    embedding_provider: EmbeddingProvider,
    *,
    knowledge_source: KnowledgeSource,
    text: str,
) -> GenerationOutcome:
    """
    Generate candidate FAQ entries from a processed file/website source's
    text and save each as a real FaqEntry, returning what the run managed.

    Individual failures are still swallowed - a provider error on one
    window, malformed output, a single entry's embedding call failing - so
    that one bad window costs its own questions and not the rest. What is no
    longer swallowed is the summary: the caller needs to know whether the
    whole document was read, because that is the difference between a source
    that is finished and one that is not.

    The source is covered window by window rather than by its opening alone,
    so the number of entries scales with how much the document actually says.
    """

    if not text.strip() or knowledge_source.assistant_id is None:
        return GenerationOutcome(windows=0, windows_covered=0, entries=0)

    budget = settings.faq_generation_tokens_per_minute
    windows = _windows(text, max_chars=window_chars_for_budget(budget))

    # Sequential, not concurrent, for two reasons that happen to agree. A
    # window can only avoid repeating earlier questions if it is told what
    # they were, which requires the earlier ones to have finished. And
    # concurrency is what provoked the provider into rate-limiting most of a
    # document - measured at 3 of 13 windows answered.
    #
    # Sequential alone was not enough. The provider's limit is tokens per
    # minute, not calls at once, so a document large enough to matter still
    # spent the whole allowance partway through and had every remaining
    # window refused. The limiter queues the windows behind that budget
    # instead: send what fits, wait for the window to roll, send the next.
    rate_limiter = TokenRateLimiter(budget=budget)

    pair_groups: list[list[tuple[str, str]]] = []
    already_asked: list[str] = []

    exhausted: LLMQuotaExhausted | None = None

    for window in windows:
        try:
            pairs_for_window = await _generate_for_window(
                llm_provider,
                window,
                already_asked=already_asked,
                knowledge_source_id=knowledge_source.id,
                rate_limiter=rate_limiter,
            )
        except LLMQuotaExhausted as exc:
            # Keep what earlier windows produced: a partial FAQ list is
            # worth having, and throwing it away would mean the allowance
            # was spent for nothing.
            exhausted = exc

            logger.warning(
                "FAQ generation stopped early for knowledge source %s after "
                "%d of %d windows: the provider's %s allowance is exhausted. "
                "Entries written so far are kept; the rest of the document "
                "was not read.",
                knowledge_source.id,
                len(pair_groups),
                len(windows),
                exc.exhausted_window or "token",
            )

            break

        pair_groups.append(pairs_for_window)
        already_asked.extend(question for question, _answer in pairs_for_window)

    pairs = _deduplicate(pair_groups)

    productive = sum(1 for group in pair_groups if group)

    if exhausted is None and productive < len(windows):
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
        return GenerationOutcome(
            windows=len(windows),
            windows_covered=len(pair_groups),
            entries=0,
            stopped_reason=_stopped_reason(exhausted),
        )

    faq_source = await _get_or_create_generated_faq_source(
        db,
        organization_id=knowledge_source.organization_id,
        workspace_id=knowledge_source.workspace_id,
        assistant_id=knowledge_source.assistant_id,
        owner_user_id=knowledge_source.owner_user_id,
    )

    saved = 0

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
        else:
            saved += 1

    return GenerationOutcome(
        windows=len(windows),
        windows_covered=len(pair_groups),
        entries=saved,
        stopped_reason=_stopped_reason(exhausted),
    )
