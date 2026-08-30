import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk


@dataclass(frozen=True)
class ChunkWrite:
    text: str
    metadata: dict
    embedding: list[float] | None = None


async def list_for_source(
    db: AsyncSession, knowledge_source_id: uuid.UUID
) -> list[Chunk]:
    """
    Every chunk for one knowledge source, in order.
    """

    result = await db.scalars(
        select(Chunk)
        .where(Chunk.knowledge_source_id == knowledge_source_id)
        .order_by(Chunk.ordering),
    )

    return list(result.all())


async def replace_for_source(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    chunks: list[ChunkWrite],
) -> list[Chunk]:
    """
    Replace a source's entire chunk set: delete whatever is there, insert
    the given writes with sequential ordering. Used for file and website
    sources, which re-derive their whole chunk set from scratch on every
    (re)process - never called before a parse/chunk/embed attempt has
    already succeeded, so a failed reprocess never destroys chunks a prior
    successful run produced.
    """

    await db.execute(
        delete(Chunk).where(Chunk.knowledge_source_id == knowledge_source_id)
    )

    rows = [
        Chunk(
            organization_id=organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
            text=write.text,
            ordering=ordering,
            chunk_metadata=write.metadata,
            embedding=write.embedding,
        )
        for ordering, write in enumerate(chunks)
    ]
    db.add_all(rows)
    await db.flush()

    return rows


async def get_for_faq_entry(
    db: AsyncSession,
    *,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
) -> Chunk | None:
    """
    The one chunk backing a manual-FAQ entry, looked up by the
    faq_entry_id recorded in its metadata.
    """

    return await db.scalar(
        select(Chunk).where(
            Chunk.knowledge_source_id == knowledge_source_id,
            Chunk.chunk_metadata["faq_entry_id"].astext == str(faq_entry_id),
        ),
    )


async def upsert_for_faq_entry(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
    text: str,
    embedding: list[float] | None = None,
) -> Chunk:
    """
    Insert or update the one chunk backing a manual-FAQ entry. Ordering is
    always 0 - FAQ chunks are independent, order-insensitive documents, not
    a sequence the way a parsed file's chunks are.
    """

    existing = await get_for_faq_entry(
        db, knowledge_source_id=knowledge_source_id, faq_entry_id=faq_entry_id
    )

    if existing is not None:
        existing.text = text
        existing.embedding = embedding
        await db.flush()

        return existing

    chunk = Chunk(
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
        text=text,
        ordering=0,
        chunk_metadata={"faq_entry_id": str(faq_entry_id)},
        embedding=embedding,
    )
    db.add(chunk)
    await db.flush()

    return chunk


async def delete_for_faq_entry(
    db: AsyncSession,
    *,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
) -> None:
    """
    Remove the one chunk backing a manual-FAQ entry, if any.
    """

    chunk = await get_for_faq_entry(
        db, knowledge_source_id=knowledge_source_id, faq_entry_id=faq_entry_id
    )

    if chunk is not None:
        await db.delete(chunk)
        await db.flush()
