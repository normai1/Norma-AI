import uuid

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization_member import OrganizationMember
from app.models.user import User


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
) -> OrganizationMember:
    """
    Add a user to an organization.
    """

    member = OrganizationMember(
        organization_id=organization_id,
        user_id=user_id,
        role=role,
    )

    db.add(member)
    await db.flush()

    return member


async def get_membership(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> OrganizationMember | None:
    """
    Return one user's membership of one organization, or None.
    """

    return await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id,
        ),
    )


async def get_by_id(
    db: AsyncSession,
    organization_id: uuid.UUID,
    member_id: uuid.UUID,
) -> OrganizationMember | None:
    """
    Look up a membership by its own id, scoped to its organization.

    The organization filter is what stops a caller in one organization from
    addressing a membership row that belongs to another.
    """

    return await db.scalar(
        select(OrganizationMember).where(
            OrganizationMember.id == member_id,
            OrganizationMember.organization_id == organization_id,
        ),
    )


def _members_query(organization_id: uuid.UUID) -> Select:
    return (
        select(OrganizationMember, User)
        .join(User, User.id == OrganizationMember.user_id)
        .where(OrganizationMember.organization_id == organization_id)
        .order_by(OrganizationMember.created_at)
    )


async def list_members(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> list[tuple[OrganizationMember, User]]:
    """
    Return every membership in an organization with its user, in one join.
    """

    result = await db.execute(_members_query(organization_id))

    return [(member, user) for member, user in result.all()]


async def count_by_role_for_update(
    db: AsyncSession,
    organization_id: uuid.UUID,
    role: str,
) -> int:
    """
    Count members holding a role, locking those rows for the transaction.

    The rows are selected rather than counted in SQL because PostgreSQL rejects
    FOR UPDATE alongside an aggregate. Holding the lock is the point: a plain
    count lets two concurrent demotions each see two owners and both proceed,
    leaving the organization with none.
    """

    result = await db.scalars(
        select(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == role,
        )
        .with_for_update(),
    )

    return len(result.all())


async def delete(db: AsyncSession, member: OrganizationMember) -> None:
    """
    Remove a membership.
    """

    await db.delete(member)
    await db.flush()
