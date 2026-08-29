import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.permissions import (
    CREATE_INVITATIONS,
    MANAGE_ASSISTANTS,
    MANAGE_MEMBERS,
    MANAGE_ORGANIZATION,
    MANAGE_WORKSPACES,
    REVOKE_INVITATIONS,
    has_permission,
)
from app.models.organization_member import OrganizationMember
from app.services import organization as organization_service

# Deliberately 404 rather than 403 for both "no such organization" and "not a
# member": telling the two apart would let any signed-in user probe which
# organization ids exist.
_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Organization not found",
)


async def require_org_member(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> OrganizationMember:
    """
    Resolve the caller's membership of the organization named in the path.

    Every organization-scoped route depends on this. The path parameter must be
    named `organization_id` for FastAPI to inject it here.
    """

    membership = await organization_service.get_membership(
        db,
        organization_id,
        current_user.id,
    )

    if membership is None:
        raise _NOT_FOUND

    return membership


CurrentOrgMembership = Annotated[OrganizationMember, Depends(require_org_member)]


def require_permission(permission: str) -> Callable:
    """
    Build a dependency that also requires the caller's role to hold a permission.

    Unlike missing membership, an insufficient permission is a genuine 403: the
    caller has already proven the organization exists to them, so there is
    nothing left to leak.
    """

    async def dependency(membership: CurrentOrgMembership) -> OrganizationMember:
        if not has_permission(membership.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not allow this action",
            )

        return membership

    return dependency


CanManageOrganization = Annotated[
    OrganizationMember,
    Depends(require_permission(MANAGE_ORGANIZATION)),
]

CanManageMembers = Annotated[
    OrganizationMember,
    Depends(require_permission(MANAGE_MEMBERS)),
]

CanCreateInvitations = Annotated[
    OrganizationMember,
    Depends(require_permission(CREATE_INVITATIONS)),
]

CanRevokeInvitations = Annotated[
    OrganizationMember,
    Depends(require_permission(REVOKE_INVITATIONS)),
]

CanManageWorkspaces = Annotated[
    OrganizationMember,
    Depends(require_permission(MANAGE_WORKSPACES)),
]

CanManageAssistants = Annotated[
    OrganizationMember,
    Depends(require_permission(MANAGE_ASSISTANTS)),
]
