import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import GlossaryEntryAlreadyExists, GlossaryEntryNotFound
from app.models.glossary_entry import GlossaryEntry
from app.repositories import glossary_entry as glossary_entry_repo
from app.repositories.glossary_entry import _UNSET
from app.services.assistant import resolve_assistant


async def resolve_glossary_entry(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    glossary_entry_id: uuid.UUID,
) -> GlossaryEntry:
    """
    Look up a glossary entry, refusing one outside the caller's assistant.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    glossary_entry = await glossary_entry_repo.get_by_id(db, glossary_entry_id)

    if glossary_entry is None or glossary_entry.assistant_id != assistant.id:
        raise GlossaryEntryNotFound

    return glossary_entry


async def create_glossary_entry(
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
    Add a glossary entry to an assistant the caller may manage, refusing one
    outside the caller's workspace.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    if await glossary_entry_repo.get_by_term(db, assistant.id, term):
        raise GlossaryEntryAlreadyExists

    try:
        async with db.begin_nested():
            return await glossary_entry_repo.create(
                db,
                organization_id=assistant.organization_id,
                workspace_id=assistant.workspace_id,
                assistant_id=assistant.id,
                term=term,
                meaning=meaning,
                phonetic_spelling=phonetic_spelling,
                stt_boost_weight=stt_boost_weight,
            )
    except IntegrityError as exc:
        # Two concurrent creates can both pass the check above; the unique
        # constraint is the real guard.
        raise GlossaryEntryAlreadyExists from exc


async def list_glossary_entries(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> list[GlossaryEntry]:
    """
    Every glossary entry for an assistant, refusing one outside the caller's
    workspace.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    return await glossary_entry_repo.list_for_assistant(db, assistant.id)


async def update_glossary_entry(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    glossary_entry_id: uuid.UUID,
    term: str = _UNSET,
    meaning: str | None = _UNSET,
    phonetic_spelling: str | None = _UNSET,
    stt_boost_weight: float = _UNSET,
) -> GlossaryEntry:
    """
    Apply a partial update to a glossary entry the caller may manage.
    """

    glossary_entry = await resolve_glossary_entry(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        glossary_entry_id=glossary_entry_id,
    )

    try:
        async with db.begin_nested():
            return await glossary_entry_repo.update(
                db,
                glossary_entry,
                term=term,
                meaning=meaning,
                phonetic_spelling=phonetic_spelling,
                stt_boost_weight=stt_boost_weight,
            )
    except IntegrityError as exc:
        raise GlossaryEntryAlreadyExists from exc


async def delete_glossary_entry(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    glossary_entry_id: uuid.UUID,
) -> None:
    """
    Permanently remove a glossary entry the caller may manage.
    """

    glossary_entry = await resolve_glossary_entry(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        glossary_entry_id=glossary_entry_id,
    )

    await glossary_entry_repo.delete(db, glossary_entry)
