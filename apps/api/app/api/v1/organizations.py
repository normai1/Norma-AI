import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession
from app.api.org_deps import CurrentOrgMembership, OrgAdmin
from app.core.exceptions import (
    LastOwnerRemoval,
    MemberNotFound,
    RoleEscalation,
    SlugGenerationFailed,
)
from app.models.organization import Organization
from app.repositories import organization as organization_repo
from app.repositories import user as user_repo
from app.schemas.organization import (
    MemberResponse,
    MemberRoleUpdate,
    MemberUserResponse,
    OrganizationCreate,
    OrganizationMembershipResponse,
    OrganizationUpdate,
)
from app.services import organization as organization_service

router = APIRouter(prefix="/organizations", tags=["organizations"])

_MEMBER_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Member not found",
)

_ROLE_ESCALATION = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Your role does not allow this change",
)

_LAST_OWNER = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="An organization must always have at least one owner",
)


def _membership_response(
    organization: Organization,
    role: str,
) -> OrganizationMembershipResponse:
    """
    Combine an organization with the caller's role in it.
    """

    return OrganizationMembershipResponse(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        settings=organization.settings,
        status=organization.status,
        created_at=organization.created_at,
        role=role,
    )


@router.post(
    "",
    response_model=OrganizationMembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    payload: OrganizationCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> OrganizationMembershipResponse:
    """
    Create an organization. The caller becomes its owner.
    """

    try:
        organization, member = await organization_service.create_organization(
            db,
            name=payload.name,
            owner_id=current_user.id,
        )
    except SlugGenerationFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not allocate an organization address. Please retry.",
        ) from exc

    await db.commit()

    return _membership_response(organization, member.role)


@router.get("", response_model=list[OrganizationMembershipResponse])
async def list_organizations(
    current_user: CurrentUser,
    db: DbSession,
) -> list[OrganizationMembershipResponse]:
    """
    List the organizations the caller belongs to.
    """

    rows = await organization_repo.list_for_user(db, current_user.id)

    return [
        _membership_response(organization, role) for organization, role in rows
    ]


@router.get(
    "/{organization_id}",
    response_model=OrganizationMembershipResponse,
)
async def get_organization(
    membership: CurrentOrgMembership,
    db: DbSession,
) -> OrganizationMembershipResponse:
    """
    Return one organization the caller belongs to.
    """

    organization = await organization_repo.get_by_id(
        db,
        membership.organization_id,
    )

    return _membership_response(organization, membership.role)


@router.patch(
    "/{organization_id}",
    response_model=OrganizationMembershipResponse,
)
async def update_organization(
    payload: OrganizationUpdate,
    membership: OrgAdmin,
    db: DbSession,
) -> OrganizationMembershipResponse:
    """
    Update an organization's name or settings. Owners and admins only.
    """

    organization = await organization_repo.get_by_id(
        db,
        membership.organization_id,
    )

    await organization_repo.update(
        db,
        organization,
        name=payload.name,
        settings=payload.settings,
    )
    await db.commit()

    return _membership_response(organization, membership.role)


@router.get(
    "/{organization_id}/members",
    response_model=list[MemberResponse],
)
async def list_members(
    membership: CurrentOrgMembership,
    db: DbSession,
) -> list[MemberResponse]:
    """
    List everyone in the organization. Any member may see the roster.
    """

    rows = await organization_service.list_members(db, membership.organization_id)

    return [
        MemberResponse(
            id=member.id,
            role=member.role,
            created_at=member.created_at,
            user=MemberUserResponse.model_validate(user),
        )
        for member, user in rows
    ]


@router.patch(
    "/{organization_id}/members/{member_id}",
    response_model=MemberResponse,
)
async def change_member_role(
    member_id: uuid.UUID,
    payload: MemberRoleUpdate,
    membership: OrgAdmin,
    db: DbSession,
) -> MemberResponse:
    """
    Change a member's role. Owners and admins only.
    """

    try:
        member = await organization_service.change_member_role(
            db,
            organization_id=membership.organization_id,
            member_id=member_id,
            new_role=payload.role,
            actor=membership,
        )
    except MemberNotFound as exc:
        raise _MEMBER_NOT_FOUND from exc
    except RoleEscalation as exc:
        raise _ROLE_ESCALATION from exc
    except LastOwnerRemoval as exc:
        raise _LAST_OWNER from exc

    await db.commit()

    user = await user_repo.get_by_id(db, member.user_id)

    return MemberResponse(
        id=member.id,
        role=member.role,
        created_at=member.created_at,
        user=MemberUserResponse.model_validate(user),
    )


@router.delete(
    "/{organization_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    member_id: uuid.UUID,
    membership: OrgAdmin,
    db: DbSession,
) -> Response:
    """
    Remove a member from the organization. Owners and admins only.
    """

    try:
        await organization_service.remove_member(
            db,
            organization_id=membership.organization_id,
            member_id=member_id,
            actor=membership,
        )
    except MemberNotFound as exc:
        raise _MEMBER_NOT_FOUND from exc
    except RoleEscalation as exc:
        raise _ROLE_ESCALATION from exc
    except LastOwnerRemoval as exc:
        raise _LAST_OWNER from exc

    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
