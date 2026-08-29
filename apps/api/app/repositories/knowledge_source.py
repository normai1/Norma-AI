import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.knowledge_source import KnowledgeSource

FILE_TYPE = "file"


async def get_by_id(
    db: AsyncSession, knowledge_source_id: uuid.UUID
) -> KnowledgeSource | None:
    """
    Look up a knowledge source by primary key.
    """

    return await db.scalar(
        select(KnowledgeSource).where(KnowledgeSource.id == knowledge_source_id),
    )


async def get_document_for_source(
    db: AsyncSession, knowledge_source_id: uuid.UUID
) -> Document | None:
    """
    The one document belonging to a knowledge source, if any.
    """

    return await db.scalar(
        select(Document).where(Document.knowledge_source_id == knowledge_source_id),
    )


async def list_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[KnowledgeSource]:
    """
    Every knowledge source in a workspace.
    """

    result = await db.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.workspace_id == workspace_id)
        .order_by(KnowledgeSource.created_at),
    )

    return list(result.all())


async def list_documents_for_sources(
    db: AsyncSession,
    knowledge_source_ids: list[uuid.UUID],
) -> list[Document]:
    """
    Every document belonging to any of the given knowledge sources - one
    query for a whole list page, not one query per row.
    """

    if not knowledge_source_ids:
        return []

    result = await db.scalars(
        select(Document).where(
            Document.knowledge_source_id.in_(knowledge_source_ids),
        ),
    )

    return list(result.all())


async def create_with_document(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    filename: str,
    content_type: str,
    storage_key: str,
) -> tuple[KnowledgeSource, Document]:
    """
    Insert a KnowledgeSource and its one Document together.
    """

    knowledge_source = KnowledgeSource(
        organization_id=organization_id,
        workspace_id=workspace_id,
        type=FILE_TYPE,
        owner_user_id=owner_user_id,
    )
    db.add(knowledge_source)
    await db.flush()

    document = Document(
        knowledge_source_id=knowledge_source.id,
        filename=filename,
        content_type=content_type,
        storage_key=storage_key,
    )
    db.add(document)
    await db.flush()

    return knowledge_source, document
