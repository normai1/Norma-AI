import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.organization_member import OrganizationMember


async def get_by_id(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> Organization | None:
    """
    Look up an organization by primary key.
    """

    return await db.scalar(
        select(Organization).where(Organization.id == organization_id),
    )


async def get_by_slug(db: AsyncSession, slug: str) -> Organization | None:
    """
    Look up an organization by its unique slug.
    """

    return await db.scalar(select(Organization).where(Organization.slug == slug))


async def list_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[tuple[Organization, str]]:
    """
    Return every organization the user belongs to, paired with their role.

    One join rather than a membership query followed by per-row organization
    lookups, so the cost does not grow with the number of memberships.
    """

    result = await db.execute(
        select(Organization, OrganizationMember.role)
        .join(
            OrganizationMember,
            OrganizationMember.organization_id == Organization.id,
        )
        .where(OrganizationMember.user_id == user_id)
        .order_by(Organization.created_at),
    )

    return [(organization, role) for organization, role in result.all()]


async def create(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
) -> Organization:
    """
    Insert a new organization.
    """

    organization = Organization(name=name, slug=slug)

    db.add(organization)
    await db.flush()

    return organization


async def update(
    db: AsyncSession,
    organization: Organization,
    *,
    name: str | None = None,
    settings: dict[str, Any] | None = None,
) -> Organization:
    """
    Apply a partial update. Fields left as None are untouched.
    """

    if name is not None:
        organization.name = name

    if settings is not None:
        organization.settings = settings

    await db.flush()

    return organization
