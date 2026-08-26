import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    LastOwnerRemoval,
    MemberNotFound,
    RoleEscalation,
    SlugGenerationFailed,
)
from app.core.slug import random_suffix, slugify
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember, outranks
from app.models.user import User
from app.repositories import organization as organization_repo
from app.repositories import organization_member as member_repo

OWNER_ROLE = "owner"

MAX_SLUG_ATTEMPTS = 5


async def create_organization(
    db: AsyncSession,
    *,
    name: str,
    owner_id: uuid.UUID,
) -> tuple[Organization, OrganizationMember]:
    """
    Create an organization and the creator's owner membership together.
    """

    base_slug = slugify(name)

    for attempt in range(MAX_SLUG_ATTEMPTS):
        slug = base_slug if attempt == 0 else f"{base_slug}-{random_suffix()}"

        if await organization_repo.get_by_slug(db, slug) is not None:
            continue

        try:
            async with db.begin_nested():
                organization = await organization_repo.create(
                    db,
                    name=name,
                    slug=slug,
                )
        except IntegrityError:
            # Two concurrent creates can both pass the check above; the unique
            # index is the real guard, so treat a collision as another attempt.
            continue

        member = await member_repo.create(
            db,
            organization_id=organization.id,
            user_id=owner_id,
            role=OWNER_ROLE,
        )

        return organization, member

    raise SlugGenerationFailed


async def get_membership(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> OrganizationMember | None:
    """
    Return the caller's membership of an organization, or None.
    """

    return await member_repo.get_membership(db, organization_id, user_id)


async def list_members(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> list[tuple[OrganizationMember, User]]:
    """
    Every membership in an organization, paired with its user.
    """

    return await member_repo.list_members(db, organization_id)


async def change_member_role(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    member_id: uuid.UUID,
    new_role: str,
    actor: OrganizationMember,
) -> OrganizationMember:
    """
    Change one member's role, within what the acting member may grant.
    """

    member = await member_repo.get_by_id(db, organization_id, member_id)

    if member is None:
        raise MemberNotFound

    # Changing your own role is never allowed. Without this an admin simply
    # grants itself owner. An owner handing over does so by promoting the
    # successor first, who can then demote them.
    if member.id == actor.id:
        raise RoleEscalation

    _guard_role_change(actor=actor, target_role=member.role, new_role=new_role)

    if member.role == new_role:
        return member

    if member.role == OWNER_ROLE:
        await _guard_last_owner(db, organization_id)

    member.role = new_role
    await db.flush()

    return member


async def remove_member(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    member_id: uuid.UUID,
    actor: OrganizationMember,
) -> None:
    """
    Remove a member, refusing to remove the last owner or anyone senior.
    """

    member = await member_repo.get_by_id(db, organization_id, member_id)

    if member is None:
        raise MemberNotFound

    # Leaving on your own account is fine; the last-owner guard below still
    # applies. Removing someone who outranks you is not.
    if member.id != actor.id and outranks(member.role, actor.role):
        raise RoleEscalation

    if member.role == OWNER_ROLE:
        await _guard_last_owner(db, organization_id)

    await member_repo.delete(db, member)


def _guard_role_change(
    *,
    actor: OrganizationMember,
    target_role: str,
    new_role: str,
) -> None:
    """
    Refuse a role change that reaches above the acting member.
    """

    # Cannot hand out a role you do not hold yourself, so an admin can never
    # mint an owner.
    if outranks(new_role, actor.role):
        raise RoleEscalation

    # Cannot act on someone senior to you, so an admin can never demote an
    # owner as a first step toward removing them.
    if outranks(target_role, actor.role):
        raise RoleEscalation


async def _guard_last_owner(db: AsyncSession, organization_id: uuid.UUID) -> None:
    """
    Refuse a change that would leave the organization with no owner.

    An organization with no owner cannot be administered by anyone, and nothing
    in the product can restore one, so this is a dead end rather than a
    recoverable state.
    """

    owners = await member_repo.count_by_role_for_update(
        db,
        organization_id,
        OWNER_ROLE,
    )

    if owners <= 1:
        raise LastOwnerRemoval
