import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession
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


def require_org_role(*allowed_roles: str) -> Callable:
    """
    Build a dependency that also requires one of the given roles.

    Minimal on purpose: feature 3 (RBAC) replaces this with a real permission
    model. Until then, membership management needs some role gate and this is
    the smallest one that does the job. Unlike missing membership, an
    insufficient role is a genuine 403: the caller has already proven the
    organization exists to them, so there is nothing left to leak.
    """

    async def dependency(membership: CurrentOrgMembership) -> OrganizationMember:
        if membership.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role does not allow this action",
            )

        return membership

    return dependency


OrgAdmin = Annotated[
    OrganizationMember,
    Depends(require_org_role("owner", "admin")),
]

OrgOwner = Annotated[OrganizationMember, Depends(require_org_role("owner"))]
