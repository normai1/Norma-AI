import uuid

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace_member import WorkspaceMember


async def get(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> WorkspaceMember | None:
    """
    Look up a user's membership of a workspace, if any.
    """

    return await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        ),
    )


async def get_by_id(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    workspace_member_id: uuid.UUID,
) -> WorkspaceMember | None:
    """
    Look up a membership by its own id, scoped to its workspace.

    The workspace filter is what stops a caller addressing a membership row
    that belongs to a different workspace.
    """

    return await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.id == workspace_member_id,
            WorkspaceMember.workspace_id == workspace_id,
        ),
    )


def _members_query(workspace_id: uuid.UUID) -> Select:
    return (
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at)
    )


async def list_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[tuple[WorkspaceMember, User]]:
    """
    Return every membership of a workspace with its user, in one join.
    """

    result = await db.execute(_members_query(workspace_id))

    return [(member, user) for member, user in result.all()]


async def create(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> WorkspaceMember:
    """
    Grant a user access to a workspace.
    """

    member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id)

    db.add(member)
    await db.flush()

    return member


async def delete(db: AsyncSession, member: WorkspaceMember) -> None:
    """
    Revoke a workspace membership.
    """

    await db.delete(member)
    await db.flush()
