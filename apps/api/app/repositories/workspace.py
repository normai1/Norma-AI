import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember


async def get_by_id(db: AsyncSession, workspace_id: uuid.UUID) -> Workspace | None:
    """
    Look up a workspace by primary key.
    """

    return await db.scalar(select(Workspace).where(Workspace.id == workspace_id))


async def list_for_organization(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> list[Workspace]:
    """
    Every workspace in an organization, regardless of membership.
    """

    result = await db.scalars(
        select(Workspace)
        .where(Workspace.organization_id == organization_id)
        .order_by(Workspace.created_at),
    )

    return list(result.all())


async def list_for_user(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[Workspace]:
    """
    Workspaces in an organization the user is an explicit member of.
    """

    result = await db.scalars(
        select(Workspace)
        .join(
            WorkspaceMember,
            WorkspaceMember.workspace_id == Workspace.id,
        )
        .where(
            Workspace.organization_id == organization_id,
            WorkspaceMember.user_id == user_id,
        )
        .order_by(Workspace.created_at),
    )

    return list(result.all())


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str,
    settings: dict[str, Any],
) -> Workspace:
    """
    Insert a new workspace. settings is required, not defaulted, so a call
    site that forgets it fails at review rather than silently falling through
    to the raw DB-level default.
    """

    workspace = Workspace(
        organization_id=organization_id,
        name=name,
        settings=settings,
    )

    db.add(workspace)
    await db.flush()

    return workspace


async def update(
    db: AsyncSession,
    workspace: Workspace,
    *,
    name: str | None = None,
    settings: dict[str, Any] | None = None,
) -> Workspace:
    """
    Apply a partial update. Fields left as None are untouched.
    """

    if name is not None:
        workspace.name = name

    if settings is not None:
        workspace.settings = settings

    await db.flush()

    return workspace
