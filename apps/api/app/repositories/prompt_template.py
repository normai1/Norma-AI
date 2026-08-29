import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_template import PromptTemplate

ARCHIVED_STATUS = "archived"
PUBLISHED_STATUS = "published"


async def get_by_id(
    db: AsyncSession, prompt_template_id: uuid.UUID
) -> PromptTemplate | None:
    """
    Look up a prompt template by primary key.
    """

    return await db.scalar(
        select(PromptTemplate).where(PromptTemplate.id == prompt_template_id),
    )


async def list_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[PromptTemplate]:
    """
    Every prompt template in a workspace.
    """

    result = await db.scalars(
        select(PromptTemplate)
        .where(PromptTemplate.workspace_id == workspace_id)
        .order_by(PromptTemplate.created_at),
    )

    return list(result.all())


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    use_case: str,
) -> PromptTemplate:
    """
    Insert a new prompt template, in the default 'draft' status.
    """

    prompt_template = PromptTemplate(
        organization_id=organization_id,
        workspace_id=workspace_id,
        name=name,
        use_case=use_case,
    )

    db.add(prompt_template)
    await db.flush()

    return prompt_template


async def update_name(
    db: AsyncSession,
    prompt_template: PromptTemplate,
    *,
    name: str,
) -> PromptTemplate:
    """
    Rename a prompt template.
    """

    prompt_template.name = name

    await db.flush()

    return prompt_template


async def archive(db: AsyncSession, prompt_template: PromptTemplate) -> PromptTemplate:
    """
    Move a prompt template to the 'archived' status. Idempotent: archiving
    an already-archived template is a no-op rather than an error.
    """

    prompt_template.status = ARCHIVED_STATUS

    await db.flush()

    return prompt_template


async def publish(
    db: AsyncSession,
    prompt_template: PromptTemplate,
    *,
    version_id: uuid.UUID,
) -> PromptTemplate:
    """
    Point a prompt template at a version and move it to 'published'.
    Idempotent: publishing the already-current version, or re-publishing an
    already-published template, is a no-op beyond the (possibly unchanged)
    pointer.
    """

    prompt_template.current_version_id = version_id
    prompt_template.status = PUBLISHED_STATUS

    await db.flush()

    return prompt_template
