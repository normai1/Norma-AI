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

import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_source import KnowledgeSource
from app.providers.embedding import EmbeddingProvider
from app.providers.llm import LLMProvider, LLMProviderError
from app.repositories import knowledge_source as knowledge_source_repo
from app.services import faq_entry as faq_entry_service

logger = logging.getLogger(__name__)

GENERATED_FAQ_SOURCE_NAME = "Generated FAQs"

# Upper bound on how many Q&A pairs one generation run creates - keeps LLM
# cost and the resulting FAQ list bounded, regardless of source length.
MAX_GENERATED_ENTRIES = 8

# Bounded so one very large document doesn't blow the prompt's context
# window or generation cost - the first ~12,000 characters of a knowledge
# source's text is plenty for a representative FAQ set.
MAX_SOURCE_TEXT_CHARS = 12_000

_SYSTEM_PROMPT = (
    "You write concise customer-facing FAQ entries for a business phone "
    "assistant. You are given raw text from one of the business's own "
    "documents or web pages. Produce realistic questions a caller might "
    "ask this business, with answers grounded ONLY in the given text - "
    "never invent a fact, price, hours, or policy that is not present in "
    "it. Respond with nothing but a JSON array of objects, each with a "
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

    return pairs[:MAX_GENERATED_ENTRIES]


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
    )
    db.add(knowledge_source)
    await db.flush()

    return knowledge_source


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
    """

    if not text.strip() or knowledge_source.assistant_id is None:
        return

    try:
        raw_response = await llm_provider.generate(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=text[:MAX_SOURCE_TEXT_CHARS],
        )
    except LLMProviderError:
        logger.warning(
            "FAQ generation failed for knowledge source %s",
            knowledge_source.id,
            exc_info=True,
        )
        return

    pairs = _extract_pairs(raw_response)

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
            )
        except Exception:
            logger.warning(
                "Failed to save a generated FAQ entry for knowledge "
                "source %s",
                knowledge_source.id,
                exc_info=True,
            )
