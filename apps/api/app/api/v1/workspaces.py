import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.org_deps import CanManageWorkspaces, CurrentOrgMembership
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import WorkspaceNotFound
from app.schemas.workspace import WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate
from app.services import workspace as workspace_service

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)

router = APIRouter(tags=["workspaces"])


@router.post(
    "/organizations/{organization_id}/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    payload: WorkspaceCreate,
    membership: CanManageWorkspaces,
    db: DbSession,
) -> WorkspaceResponse:
    """
    Create a workspace. Owners and admins only.
    """

    workspace = await workspace_service.create_workspace(
        db,
        organization_id=membership.organization_id,
        name=payload.name,
    )

    await db.commit()

    return WorkspaceResponse.model_validate(workspace)


@router.get(
    "/organizations/{organization_id}/workspaces",
    response_model=list[WorkspaceResponse],
)
async def list_workspaces(
    membership: CurrentOrgMembership,
    db: DbSession,
) -> list[WorkspaceResponse]:
    """
    List workspaces the caller may see: all of them for a manager, otherwise
    only the ones they are an explicit member of.
    """

    workspaces = await workspace_service.list_workspaces(db, membership=membership)

    return [WorkspaceResponse.model_validate(workspace) for workspace in workspaces]


@router.get(
    "/organizations/{organization_id}/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
)
async def get_workspace(workspace: CurrentWorkspace) -> WorkspaceResponse:
    """
    Return one workspace the caller may access.
    """

    return WorkspaceResponse.model_validate(workspace)


@router.patch(
    "/organizations/{organization_id}/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
)
async def update_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceUpdate,
    membership: CanManageWorkspaces,
    db: DbSession,
) -> WorkspaceResponse:
    """
    Update a workspace's name or settings. Owners and admins only.
    """

    try:
        workspace = await workspace_service.update_workspace(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            name=payload.name,
            settings=payload.settings,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc

    await db.commit()

    return WorkspaceResponse.model_validate(workspace)
