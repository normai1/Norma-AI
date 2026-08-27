import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import DbSession
from app.api.org_deps import CurrentOrgMembership
from app.core.permissions import MANAGE_WORKSPACES, has_permission
from app.models.workspace import Workspace
from app.repositories import workspace as workspace_repo
from app.repositories import workspace_member as workspace_member_repo

# Same information-hiding reasoning as require_org_member: "no such workspace"
# and "not granted access" must look identical.
_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)


async def require_workspace_access(
    workspace_id: uuid.UUID,
    membership: CurrentOrgMembership,
    db: DbSession,
) -> Workspace:
    """
    Resolve read access to a workspace in the path.

    An org-level workspace manager reaches any workspace in their
    organization unconditionally. Anyone else needs an explicit
    WorkspaceMember row.
    """

    workspace = await workspace_repo.get_by_id(db, workspace_id)

    if workspace is None or workspace.organization_id != membership.organization_id:
        raise _NOT_FOUND

    if has_permission(membership.role, MANAGE_WORKSPACES):
        return workspace

    member = await workspace_member_repo.get(db, workspace_id, membership.user_id)

    if member is None:
        raise _NOT_FOUND

    return workspace


CurrentWorkspace = Annotated[Workspace, Depends(require_workspace_access)]
