import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import EmailSender
from app.core.exceptions import (
    AlreadyAMember,
    InvalidInvitation,
    InvitationConflict,
    InvitationEmailMismatch,
    MemberNotFound,
    RoleEscalation,
)
from app.core.security import hash_token, normalize_email
from app.models.invitation import Invitation
from app.models.organization_member import OrganizationMember, outranks
from app.repositories import invitation as invitation_repo
from app.repositories import organization as organization_repo
from app.repositories import organization_member as member_repo
from app.repositories import user as user_repo

INVITATION_TTL_DAYS = 14

MAX_INVITE_ATTEMPTS = 3


def generate_invitation_token() -> str:
    """
    Create the opaque token that goes in an invitation link.
    """

    return secrets.token_urlsafe(48)


async def invite(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    role: str,
    inviter: OrganizationMember,
    sender: EmailSender,
) -> tuple[Invitation, str]:
    """
    Issue an invitation and return it with its plaintext token.

    The token is returned to the caller because no email provider is configured
    yet; once one is, delivery becomes the only way it reaches the invitee.
    """

    # An invitation is another way to hand out a role, so it obeys the same
    # ceiling as changing one: no inviting somebody in above yourself.
    if outranks(role, inviter.role):
        raise RoleEscalation

    normalized = normalize_email(email)

    existing_user = await user_repo.get_by_email(db, normalized)

    if existing_user is not None:
        membership = await member_repo.get_membership(
            db,
            organization_id,
            existing_user.id,
        )

        if membership is not None:
            raise AlreadyAMember

    token, invitation = await _supersede_and_create(
        db,
        organization_id=organization_id,
        email=normalized,
        role=role,
        inviter=inviter,
    )

    organization = await organization_repo.get_by_id(db, organization_id)

    await sender.send(
        to=normalized,
        subject=f"You have been invited to join {organization.name} on Norma AI",
        body="Open the invitation link to accept.",
    )

    return invitation, token


async def _supersede_and_create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    role: str,
    inviter: OrganizationMember,
) -> tuple[str, Invitation]:
    """
    Revoke any outstanding invitation for the address, then issue a new one.

    The locking read only helps once a pending row exists; on the very first
    invitation there is nothing to lock, so two concurrent callers can both find
    none and both insert. The partial unique index rejects one of them, and this
    retry turns that into the ordinary supersede path rather than a 500. Same
    shape as the slug-collision retry in `organization.create_organization`.
    """

    for _attempt in range(MAX_INVITE_ATTEMPTS):
        outstanding = await invitation_repo.get_pending_for_email_for_update(
            db,
            organization_id,
            email,
        )

        if outstanding is not None:
            outstanding.status = "revoked"
            await db.flush()

        token = generate_invitation_token()

        try:
            async with db.begin_nested():
                invitation = await invitation_repo.create(
                    db,
                    organization_id=organization_id,
                    email=email,
                    role=role,
                    token_hash=hash_token(token),
                    expires_at=datetime.now(UTC)
                    + timedelta(days=INVITATION_TTL_DAYS),
                    invited_by_user_id=inviter.user_id,
                )
        except IntegrityError:
            # A concurrent invite committed a pending row between the read and
            # this insert. Loop: the next read finds and supersedes it. The last
            # attempt falls out of the loop rather than re-raising, so callers
            # see one domain error instead of a database one.
            continue

        return token, invitation

    raise InvitationConflict


async def accept(
    db: AsyncSession,
    *,
    token: str,
    user_id: uuid.UUID,
) -> OrganizationMember:
    """
    Redeem an invitation for the signed-in user.
    """

    invitation = await invitation_repo.get_by_token_hash(db, hash_token(token))

    if invitation is None or invitation.status != "pending":
        raise InvalidInvitation

    if invitation.expires_at <= datetime.now(UTC):
        invitation.status = "expired"
        await db.flush()

        raise InvalidInvitation

    user = await user_repo.get_by_id(db, user_id)

    if user is None:
        raise InvalidInvitation

    # An invitation is addressed to one person. Letting any signed-in holder of
    # the link redeem it would turn a forwarded email into an access grant.
    if user.email != invitation.email:
        raise InvitationEmailMismatch

    existing = await member_repo.get_membership(
        db,
        invitation.organization_id,
        user_id,
    )

    if existing is not None:
        invitation.status = "accepted"
        await db.flush()

        return existing

    member = await member_repo.create(
        db,
        organization_id=invitation.organization_id,
        user_id=user_id,
        role=invitation.role,
    )

    invitation.status = "accepted"
    await db.flush()

    return member


async def revoke(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
) -> Invitation:
    """
    Withdraw a pending invitation.
    """

    invitation = await invitation_repo.get_by_id(
        db,
        organization_id,
        invitation_id,
    )

    if invitation is None:
        raise MemberNotFound

    if invitation.status != "pending":
        raise InvalidInvitation

    invitation.status = "revoked"
    await db.flush()

    return invitation


async def list_for_organization(
    db: AsyncSession,
    organization_id: uuid.UUID,
) -> list[Invitation]:
    """
    Every invitation issued for an organization.
    """

    return await invitation_repo.list_for_organization(db, organization_id)
