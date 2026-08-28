import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    MemberNotFound,
    WorkspaceMemberAlreadyExists,
    WorkspaceMemberNotFound,
    WorkspaceNotFound,
)
from app.core.permissions import MANAGE_WORKSPACES, has_permission
from app.models.organization_member import OrganizationMember
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember
from app.repositories import organization_member as organization_member_repo
from app.repositories import workspace as workspace_repo
from app.repositories import workspace_member as workspace_member_repo
from app.schemas.settings import WorkspaceSettings, WorkspaceSettingsUpdate


async def create_workspace(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str,
) -> Workspace:
    """
    Create a workspace in an organization.
    """

    return await workspace_repo.create(
        db,
        organization_id=organization_id,
        name=name,
        settings=WorkspaceSettings().model_dump(mode="json"),
    )


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


async def _resolve_workspace(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Workspace:
    """
    Look up a workspace, refusing one outside the caller's organization.
    """

    workspace = await workspace_repo.get_by_id(db, workspace_id)

    if workspace is None or workspace.organization_id != organization_id:
        raise WorkspaceNotFound

    return workspace


async def update_workspace(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str | None,
    settings: WorkspaceSettingsUpdate | None,
) -> Workspace:
    """
    Apply a partial update, refusing a workspace outside the caller's organization.

    A provided settings only merges the fields the caller actually sent,
    leaving the rest of the stored settings untouched.
    """

    workspace = await _resolve_workspace(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    merged_settings = None

    if settings is not None:
        merged = {**workspace.settings, **settings.model_dump(exclude_unset=True)}
        merged_settings = WorkspaceSettings.model_validate(merged).model_dump(
            mode="json",
        )

    return await workspace_repo.update(
        db,
        workspace,
        name=name,
        settings=merged_settings,
    )


async def add_member(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
) -> WorkspaceMember:
    """
    Grant an existing organization member access to a workspace.
    """

    workspace = await _resolve_workspace(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    org_member = await organization_member_repo.get_by_id(
        db,
        organization_id,
        member_id,
    )

    if org_member is None:
        raise MemberNotFound

    if await workspace_member_repo.get(db, workspace.id, org_member.user_id):
        raise WorkspaceMemberAlreadyExists

    try:
        async with db.begin_nested():
            return await workspace_member_repo.create(
                db,
                workspace_id=workspace.id,
                user_id=org_member.user_id,
            )
    except IntegrityError as exc:
        # Two concurrent grants can both pass the check above; the unique
        # index is the real guard.
        raise WorkspaceMemberAlreadyExists from exc


async def list_workspace_members(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[tuple[WorkspaceMember, User]]:
    """
    Every membership of a workspace, paired with its user.
    """

    return await workspace_member_repo.list_for_workspace(db, workspace_id)


async def remove_member(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_member_id: uuid.UUID,
) -> None:
    """
    Revoke a workspace membership.
    """

    workspace = await _resolve_workspace(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    member = await workspace_member_repo.get_by_id(
        db,
        workspace.id,
        workspace_member_id,
    )

    if member is None:
        raise WorkspaceMemberNotFound

    await workspace_member_repo.delete(db, member)
