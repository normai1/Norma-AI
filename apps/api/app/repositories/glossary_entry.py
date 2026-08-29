import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.glossary_entry import GlossaryEntry

# A separate sentinel marks "omitted" for update(), since meaning and
# phonetic_spelling are themselves nullable columns - an explicit None is a
# legitimate "clear this field" request, not "leave it alone" (same
# reasoning as user_repo.update's _UNSET, fixed there for F-36).
_UNSET: Any = object()


async def get_by_id(
    db: AsyncSession, glossary_entry_id: uuid.UUID
) -> GlossaryEntry | None:
    """
    Look up a glossary entry by primary key.
    """

    return await db.scalar(
        select(GlossaryEntry).where(GlossaryEntry.id == glossary_entry_id),
    )


async def get_by_term(
    db: AsyncSession,
    assistant_id: uuid.UUID,
    term: str,
) -> GlossaryEntry | None:
    """
    Look up a glossary entry by its term, scoped to one assistant.
    """

    return await db.scalar(
        select(GlossaryEntry).where(
            GlossaryEntry.assistant_id == assistant_id,
            GlossaryEntry.term == term,
        ),
    )


async def list_for_assistant(
    db: AsyncSession,
    assistant_id: uuid.UUID,
) -> list[GlossaryEntry]:
    """
    Every glossary entry for an assistant.
    """

    result = await db.scalars(
        select(GlossaryEntry)
        .where(GlossaryEntry.assistant_id == assistant_id)
        .order_by(GlossaryEntry.created_at),
    )

    return list(result.all())


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    term: str,
    meaning: str | None,
    phonetic_spelling: str | None,
    stt_boost_weight: float,
) -> GlossaryEntry:
    """
    Insert a new glossary entry.
    """

    glossary_entry = GlossaryEntry(
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        term=term,
        meaning=meaning,
        phonetic_spelling=phonetic_spelling,
        stt_boost_weight=stt_boost_weight,
    )

    db.add(glossary_entry)
    await db.flush()

    return glossary_entry


async def update(
    db: AsyncSession,
    glossary_entry: GlossaryEntry,
    *,
    term: str = _UNSET,
    meaning: str | None = _UNSET,
    phonetic_spelling: str | None = _UNSET,
    stt_boost_weight: float = _UNSET,
) -> GlossaryEntry:
    """
    Apply a partial update. An omitted argument leaves that column
    untouched; an explicit None for meaning/phonetic_spelling clears it.
    """

    if term is not _UNSET:
        glossary_entry.term = term

    if meaning is not _UNSET:
        glossary_entry.meaning = meaning

    if phonetic_spelling is not _UNSET:
        glossary_entry.phonetic_spelling = phonetic_spelling

    if stt_boost_weight is not _UNSET:
        glossary_entry.stt_boost_weight = stt_boost_weight

    await db.flush()

    return glossary_entry


async def delete(db: AsyncSession, glossary_entry: GlossaryEntry) -> None:
    """
    Permanently remove a glossary entry - a plain reference row, not a
    versioned configuration snapshot, so this is a real hard delete.
    """

    await db.delete(glossary_entry)
    await db.flush()
