import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    PromptTemplateArchived,
    PromptTemplateNotFound,
    PromptVersionNotFound,
    WorkspaceNotFound,
)
from app.models.prompt_template import PromptTemplate
from app.repositories import prompt_template as prompt_template_repo
from app.repositories import prompt_version as prompt_version_repo
from app.repositories import workspace as workspace_repo


async def _resolve_workspace_id(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    """
    Confirm the workspace exists in the caller's organization before any
    prompt template operation touches it.
    """

    workspace = await workspace_repo.get_by_id(db, workspace_id)

    if workspace is None or workspace.organization_id != organization_id:
        raise WorkspaceNotFound

    return workspace.id


async def resolve_prompt_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
) -> PromptTemplate:
    """
    Look up a prompt template, refusing one outside the caller's workspace.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    prompt_template = await prompt_template_repo.get_by_id(db, prompt_template_id)

    if prompt_template is None or prompt_template.workspace_id != workspace_id:
        raise PromptTemplateNotFound

    return prompt_template


async def create_prompt_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    use_case: str,
) -> PromptTemplate:
    """
    Create a prompt template in a workspace, refusing one outside the
    caller's organization.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    return await prompt_template_repo.create(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        name=name,
        use_case=use_case,
    )


async def list_prompt_templates(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[PromptTemplate]:
    """
    Every prompt template in a workspace, refusing one outside the caller's
    organization.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    return await prompt_template_repo.list_for_workspace(db, workspace_id)


async def get_prompt_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
) -> PromptTemplate:
    """
    Fetch one prompt template the caller may access.
    """

    return await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )


async def rename_prompt_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    name: str,
) -> PromptTemplate:
    """
    Rename a prompt template the caller may manage.
    """

    prompt_template = await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )

    return await prompt_template_repo.update_name(db, prompt_template, name=name)


async def archive_prompt_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
) -> PromptTemplate:
    """
    Archive a prompt template the caller may manage. Idempotent.
    """

    prompt_template = await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )

    return await prompt_template_repo.archive(db, prompt_template)


async def publish_prompt_template(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    version: int,
) -> PromptTemplate:
    """
    Publish a version of a prompt template the caller may manage - also how
    a rollback works, since naming an older version than the current one is
    the entire operation. Refuses an archived template: there is no restore
    path yet, so nothing could legally bring it back to life.
    """

    prompt_template = await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )

    if prompt_template.status == prompt_template_repo.ARCHIVED_STATUS:
        raise PromptTemplateArchived

    prompt_version = await prompt_version_repo.get_by_version(
        db,
        prompt_template.id,
        version,
    )

    if prompt_version is None:
        raise PromptVersionNotFound

    return await prompt_template_repo.publish(
        db,
        prompt_template,
        version_id=prompt_version.id,
    )
