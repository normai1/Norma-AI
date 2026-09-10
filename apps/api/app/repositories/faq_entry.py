import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_entry import FaqEntry
from app.models.knowledge_source import KnowledgeSource

# A separate sentinel marks "omitted" for update() - matching
# glossary_entry.py's exact reasoning, kept consistent even though neither
# question nor answer is itself nullable here (both are always required
# once set; _UNSET only distinguishes "this field was in the PATCH body"
# from "it wasn't", not a clear-vs-omit case).
_UNSET: Any = object()


async def get_by_id(db: AsyncSession, faq_entry_id: uuid.UUID) -> FaqEntry | None:
    """
    Look up a FAQ entry by primary key.
    """

    return await db.scalar(select(FaqEntry).where(FaqEntry.id == faq_entry_id))


async def list_for_source(
    db: AsyncSession,
    knowledge_source_id: uuid.UUID,
) -> list[FaqEntry]:
    """
    Every FAQ entry for one knowledge source.
    """

    result = await db.scalars(
        select(FaqEntry)
        .where(FaqEntry.knowledge_source_id == knowledge_source_id)
        .order_by(FaqEntry.created_at),
    )

    return list(result.all())


async def create(
    db: AsyncSession,
    *,
    knowledge_source_id: uuid.UUID,
    question: str,
    answer: str,
    generated_from_knowledge_source_id: uuid.UUID | None = None,
) -> FaqEntry:
    """
    Insert a new FAQ entry.

    generated_from_knowledge_source_id names the file or website source this
    entry was generated from; it stays None for an operator-authored entry.
    """

    faq_entry = FaqEntry(
        knowledge_source_id=knowledge_source_id,
        question=question,
        answer=answer,
        generated_from_knowledge_source_id=generated_from_knowledge_source_id,
    )

    db.add(faq_entry)
    await db.flush()

    return faq_entry


async def update(
    db: AsyncSession,
    faq_entry: FaqEntry,
    *,
    question: str = _UNSET,
    answer: str = _UNSET,
) -> FaqEntry:
    """
    Apply a partial update. An omitted argument leaves that column
    untouched.
    """

    if question is not _UNSET:
        faq_entry.question = question

    if answer is not _UNSET:
        faq_entry.answer = answer

    await db.flush()

    return faq_entry


async def delete(db: AsyncSession, faq_entry: FaqEntry) -> None:
    """
    Permanently remove a FAQ entry - a plain reference row, not a versioned
    snapshot, so this is a real hard delete.
    """

    await db.delete(faq_entry)
    await db.flush()


async def list_generated_from_source(
    db: AsyncSession, knowledge_source_id: uuid.UUID
) -> list[FaqEntry]:
    """
    Every entry generated from one file/website source, wherever it was
    filed. Used by the delete path, which has to remove each entry's chunk
    explicitly - that chunk is linked by a metadata key, not a foreign key,
    so no database cascade can reach it.
    """

    result = await db.scalars(
        select(FaqEntry).where(
            FaqEntry.generated_from_knowledge_source_id == knowledge_source_id
        )
    )

    return list(result)


async def list_questions_for_assistant(
    db: AsyncSession, assistant_id: uuid.UUID
) -> list[str]:
    """
    Every FAQ question text belonging to one assistant, across all of its
    knowledge sources.

    Questions only, and no ORM objects: the one caller warms an embedding
    cache with them (services/query_embedding_cache.py) and has no use for
    the answers or the rows.
    """

    result = await db.scalars(
        select(FaqEntry.question)
        .join(
            KnowledgeSource,
            KnowledgeSource.id == FaqEntry.knowledge_source_id,
        )
        .where(KnowledgeSource.assistant_id == assistant_id)
        .order_by(FaqEntry.created_at),
    )

    return list(result.all())
