import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant

ARCHIVED_STATUS = "archived"
PUBLISHED_STATUS = "published"


async def get_by_id(db: AsyncSession, assistant_id: uuid.UUID) -> Assistant | None:
    """
    Look up an assistant by primary key.
    """

    return await db.scalar(select(Assistant).where(Assistant.id == assistant_id))


async def list_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[Assistant]:
    """
    Every assistant in a workspace.
    """

    result = await db.scalars(
        select(Assistant)
        .where(Assistant.workspace_id == workspace_id)
        .order_by(Assistant.created_at),
    )

    return list(result.all())


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
) -> Assistant:
    """
    Insert a new assistant, in the default 'draft' status.
    """

    assistant = Assistant(
        organization_id=organization_id,
        workspace_id=workspace_id,
        name=name,
    )

    db.add(assistant)
    await db.flush()

    return assistant


async def update_name(
    db: AsyncSession,
    assistant: Assistant,
    *,
    name: str,
) -> Assistant:
    """
    Rename an assistant.
    """

    assistant.name = name

    await db.flush()

    return assistant


async def archive(db: AsyncSession, assistant: Assistant) -> Assistant:
    """
    Move an assistant to the 'archived' status. Idempotent: archiving an
    already-archived assistant is a no-op rather than an error.
    """

    assistant.status = ARCHIVED_STATUS

    await db.flush()

    return assistant


async def delete(db: AsyncSession, assistant: Assistant) -> None:
    """
    Permanently remove an assistant. Cascades (via existing ondelete="CASCADE"
    foreign keys) to its AssistantVersion, KnowledgeSource, Chunk, and
    GlossaryEntry rows - verified directly against the real dev database,
    including the case where current_version_id still points at a live
    version row, before this function was written.
    """

    await db.delete(assistant)
    await db.flush()


async def publish(
    db: AsyncSession,
    assistant: Assistant,
    *,
    version_id: uuid.UUID,
) -> Assistant:
    """
    Point an assistant at a version and move it to 'published'. Idempotent:
    publishing the already-current version, or re-publishing an already-
    published assistant, is a no-op beyond the (possibly unchanged) pointer.
    """

    assistant.current_version_id = version_id
    assistant.status = PUBLISHED_STATUS

    await db.flush()

    return assistant
