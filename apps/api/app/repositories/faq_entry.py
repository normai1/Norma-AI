import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_entry import FaqEntry

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
) -> FaqEntry:
    """
    Insert a new FAQ entry.
    """

    faq_entry = FaqEntry(
        knowledge_source_id=knowledge_source_id,
        question=question,
        answer=answer,
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
