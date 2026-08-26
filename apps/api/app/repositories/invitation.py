import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invitation import Invitation


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    role: str,
    token_hash: str,
    expires_at,
    invited_by_user_id: uuid.UUID | None,
) -> Invitation:
    """
    Record a new pending invitation.
    """

    invitation = Invitation(
        organization_id=organization_id,
        email=email,
        role=role,
        token_hash=token_hash,
        expires_at=expires_at,
        status="pending",
        invited_by_user_id=invited_by_user_id,
    )

    db.add(invitation)
    await db.flush()

    return invitation


async def get_by_token_hash(
    db: AsyncSession,
    token_hash: str,
) -> Invitation | None:
    """
    Find an invitation by the hash of its token.
    """

    return await db.scalar(
        select(Invitation).where(Invitation.token_hash == token_hash),
    )


async def get_by_id(
    db: AsyncSession,
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
) -> Invitation | None:
    """
    Look up an invitation scoped to its organization.
    """

    return await db.scalar(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.organization_id == organization_id,
        ),
    )


async def get_pending_for_email_for_update(
    db: AsyncSession,
    organization_id: uuid.UUID,
    email: str,
) -> Invitation | None:
    """
    Find an outstanding invitation for an address, locking it if present.

    The lock makes supersede-on-reinvite safe: a second concurrent invite waits
    here rather than reading the same row and issuing a rival token.
    """

    return await db.scalar(
        select(Invitation)
        .where(
            Invitation.organization_id == organization_id,
            Invitation.email == email,
            Invitation.status == "pending",
        )
        .with_for_update(),
    )


async def list_for_organization(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> list[Invitation]:
    """
    Every invitation ever issued for an organization, newest last.
    """

    result = await db.scalars(
        select(Invitation)
        .where(Invitation.organization_id == organization_id)
        .order_by(Invitation.created_at),
    )

    return list(result.all())
