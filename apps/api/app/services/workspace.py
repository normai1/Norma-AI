import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import WorkspaceNotFound
from app.core.permissions import MANAGE_WORKSPACES, has_permission
from app.models.organization_member import OrganizationMember
from app.models.workspace import Workspace
from app.repositories import workspace as workspace_repo


async def create_workspace(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str,
) -> Workspace:
    """
    Create a workspace in an organization.
    """

    return await workspace_repo.create(db, organization_id=organization_id, name=name)


async def list_workspaces(
    db: AsyncSession,
    *,
    membership: OrganizationMember,
) -> list[Workspace]:
    """
    Every workspace an org-level manager may see, or only the caller's own.
    """

    if has_permission(membership.role, MANAGE_WORKSPACES):
        return await workspace_repo.list_for_organization(
            db,
            membership.organization_id,
        )

    return await workspace_repo.list_for_user(
        db,
        membership.organization_id,
        membership.user_id,
    )


async def update_workspace(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str | None,
    settings: dict[str, Any] | None,
) -> Workspace:
    """
    Apply a partial update, refusing a workspace outside the caller's organization.
    """

    workspace = await workspace_repo.get_by_id(db, workspace_id)

    if workspace is None or workspace.organization_id != organization_id:
        raise WorkspaceNotFound

    return await workspace_repo.update(db, workspace, name=name, settings=settings)
