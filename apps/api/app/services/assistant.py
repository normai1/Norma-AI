import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound, WorkspaceNotFound
from app.models.assistant import Assistant
from app.repositories import assistant as assistant_repo
from app.repositories import workspace as workspace_repo


async def _resolve_workspace_id(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    """
    Confirm the workspace exists in the caller's organization before any
    assistant operation touches it.
    """

    workspace = await workspace_repo.get_by_id(db, workspace_id)

    if workspace is None or workspace.organization_id != organization_id:
        raise WorkspaceNotFound

    return workspace.id


async def _resolve_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> Assistant:
    """
    Look up an assistant, refusing one outside the caller's workspace.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None or assistant.workspace_id != workspace_id:
        raise AssistantNotFound

    return assistant


async def create_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
) -> Assistant:
    """
    Create an assistant in a workspace, refusing one outside the caller's
    organization.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    return await assistant_repo.create(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        name=name,
    )


async def list_assistants(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[Assistant]:
    """
    Every assistant in a workspace, refusing one outside the caller's
    organization.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    return await assistant_repo.list_for_workspace(db, workspace_id)


async def get_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> Assistant:
    """
    Fetch one assistant the caller may access.
    """

    return await _resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )


async def rename_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    name: str,
) -> Assistant:
    """
    Rename an assistant the caller may manage.
    """

    assistant = await _resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    return await assistant_repo.update_name(db, assistant, name=name)


async def archive_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> Assistant:
    """
    Archive an assistant the caller may manage. Idempotent.
    """

    assistant = await _resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    return await assistant_repo.archive(db, assistant)
