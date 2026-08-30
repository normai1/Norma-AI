import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FaqEntryNotFound, KnowledgeSourceNotFound
from app.models.faq_entry import FaqEntry
from app.providers.embedding import EmbeddingProvider
from app.repositories import chunk as chunk_repo
from app.repositories import faq_entry as faq_entry_repo
from app.repositories import knowledge_source as knowledge_source_repo
from app.repositories.faq_entry import _UNSET
from app.services.knowledge_source import resolve_knowledge_source


def _faq_chunk_text(question: str, answer: str) -> str:
    return f"Q: {question}\nA: {answer}"


async def _resolve_manual_faq_source_id(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> uuid.UUID:
    """
    Confirm the knowledge source exists in the caller's workspace and is
    type='manual_faq' before any FAQ entry operation touches it. A missing
    source and a wrong-type source both collapse into FaqEntryNotFound - one
    404 regardless of which underlying reason, matching this feature's own
    spec ("404 if the source doesn't exist ... or isn't type='manual_faq'").
    """

    try:
        knowledge_source = await resolve_knowledge_source(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
        )
    except KnowledgeSourceNotFound as exc:
        raise FaqEntryNotFound from exc

    if knowledge_source.type != knowledge_source_repo.MANUAL_FAQ_TYPE:
        raise FaqEntryNotFound

    return knowledge_source.id


async def resolve_faq_entry(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
) -> FaqEntry:
    """
    Look up a FAQ entry, refusing one outside the caller's manual-FAQ
    knowledge source.
    """

    source_id = await _resolve_manual_faq_source_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )

    faq_entry = await faq_entry_repo.get_by_id(db, faq_entry_id)

    if faq_entry is None or faq_entry.knowledge_source_id != source_id:
        raise FaqEntryNotFound

    return faq_entry


async def create_faq_entry(
    db: AsyncSession,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    question: str,
    answer: str,
) -> FaqEntry:
    """
    Add a FAQ entry to a manual-FAQ knowledge source the caller may manage.
    Embedding happens before the entry is created - a provider failure
    propagates to the caller (mapped to a 503 at the route) rather than
    creating an entry with no embedding; nothing is committed either way
    since the caller's db.commit() runs after this returns.
    """

    source_id = await _resolve_manual_faq_source_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )

    [vector] = await embedding_provider.embed([_faq_chunk_text(question, answer)])

    faq_entry = await faq_entry_repo.create(
        db,
        knowledge_source_id=source_id,
        question=question,
        answer=answer,
    )

    await chunk_repo.upsert_for_faq_entry(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=source_id,
        faq_entry_id=faq_entry.id,
        text=_faq_chunk_text(faq_entry.question, faq_entry.answer),
        embedding=vector,
    )

    return faq_entry


async def list_faq_entries(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> list[FaqEntry]:
    """
    Every FAQ entry for a manual-FAQ knowledge source, refusing one outside
    the caller's workspace.
    """

    source_id = await _resolve_manual_faq_source_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )

    return await faq_entry_repo.list_for_source(db, source_id)


async def update_faq_entry(
    db: AsyncSession,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
    question: str = _UNSET,
    answer: str = _UNSET,
) -> FaqEntry:
    """
    Apply a partial update to a FAQ entry the caller may manage. Embedding
    happens before the update is applied - a provider failure propagates
    to the caller (mapped to a 503 at the route) leaving the original
    entry and chunk untouched.
    """

    faq_entry = await resolve_faq_entry(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
        faq_entry_id=faq_entry_id,
    )

    new_question = faq_entry.question if question is _UNSET else question
    new_answer = faq_entry.answer if answer is _UNSET else answer
    [vector] = await embedding_provider.embed(
        [_faq_chunk_text(new_question, new_answer)]
    )

    faq_entry = await faq_entry_repo.update(
        db, faq_entry, question=question, answer=answer
    )

    await chunk_repo.upsert_for_faq_entry(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=faq_entry.knowledge_source_id,
        faq_entry_id=faq_entry.id,
        text=_faq_chunk_text(faq_entry.question, faq_entry.answer),
        embedding=vector,
    )

    return faq_entry


async def delete_faq_entry(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
) -> None:
    """
    Permanently remove a FAQ entry the caller may manage.
    """

    faq_entry = await resolve_faq_entry(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
        faq_entry_id=faq_entry_id,
    )

    await chunk_repo.delete_for_faq_entry(
        db,
        knowledge_source_id=faq_entry.knowledge_source_id,
        faq_entry_id=faq_entry.id,
    )
    await faq_entry_repo.delete(db, faq_entry)
