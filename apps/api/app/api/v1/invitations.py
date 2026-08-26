import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession
from app.api.org_deps import (
    CanCreateInvitations,
    CanRevokeInvitations,
    CurrentOrgMembership,
)
from app.core.email import EmailSender, get_email_sender
from app.core.exceptions import (
    AlreadyAMember,
    InvalidInvitation,
    InvitationConflict,
    InvitationEmailMismatch,
    MemberNotFound,
    RoleEscalation,
)
from app.schemas.invitation import (
    InvitationAccept,
    InvitationCreate,
    InvitationCreatedResponse,
    InvitationResponse,
)
from app.schemas.organization import MemberResponse, MemberUserResponse
from app.services import invitation as invitation_service

router = APIRouter(tags=["invitations"])

Sender = Annotated[EmailSender, Depends(get_email_sender)]

_INVALID_INVITATION = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="This invitation is no longer valid",
)


@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=InvitationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    payload: InvitationCreate,
    membership: CanCreateInvitations,
    db: DbSession,
    sender: Sender,
) -> InvitationCreatedResponse:
    """
    Invite an email address to join the organization. Owners and admins only.
    """

    try:
        invitation, token = await invitation_service.invite(
            db,
            organization_id=membership.organization_id,
            email=payload.email,
            role=payload.role,
            inviter=membership,
            sender=sender,
        )
    except RoleEscalation as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role does not allow inviting at that level",
        ) from exc
    except InvitationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another invitation to this address is in flight. Try again.",
        ) from exc
    except AlreadyAMember as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That person is already a member of this organization",
        ) from exc

    await db.commit()

    return InvitationCreatedResponse(
        **InvitationResponse.model_validate(invitation).model_dump(),
        token=token,
    )


@router.get(
    "/organizations/{organization_id}/invitations",
    response_model=list[InvitationResponse],
)
async def list_invitations(
    membership: CurrentOrgMembership,
    db: DbSession,
) -> list[InvitationResponse]:
    """
    List invitations issued for the organization.
    """

    invitations = await invitation_service.list_for_organization(
        db,
        membership.organization_id,
    )

    return [
        InvitationResponse.model_validate(invitation) for invitation in invitations
    ]


@router.delete(
    "/organizations/{organization_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    membership: CanRevokeInvitations,
    db: DbSession,
) -> Response:
    """
    Withdraw a pending invitation. Owners and admins only.
    """

    try:
        await invitation_service.revoke(
            db,
            organization_id=membership.organization_id,
            invitation_id=invitation_id,
        )
    except MemberNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        ) from exc
    except InvalidInvitation as exc:
        raise _INVALID_INVITATION from exc

    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/accept", response_model=MemberResponse)
async def accept_invitation(
    payload: InvitationAccept,
    current_user: CurrentUser,
    db: DbSession,
) -> MemberResponse:
    """
    Redeem an invitation token as the signed-in user.
    """

    try:
        member = await invitation_service.accept(
            db,
            token=payload.token,
            user_id=current_user.id,
        )
    except InvitationEmailMismatch as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invitation was sent to a different email address",
        ) from exc
    except InvalidInvitation as exc:
        raise _INVALID_INVITATION from exc

    await db.commit()

    return MemberResponse(
        id=member.id,
        role=member.role,
        created_at=member.created_at,
        user=MemberUserResponse.model_validate(current_user),
    )
