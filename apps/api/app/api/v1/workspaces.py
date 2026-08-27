import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import DbSession
from app.api.org_deps import CanManageWorkspaces, CurrentOrgMembership
from app.api.v1.organizations import _MEMBER_NOT_FOUND
from app.api.workspace_deps import _NOT_FOUND as _WORKSPACE_NOT_FOUND
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import (
    MemberNotFound,
    WorkspaceMemberAlreadyExists,
    WorkspaceMemberNotFound,
    WorkspaceNotFound,
)
from app.repositories import user as user_repo
from app.schemas.organization import MemberUserResponse
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceMemberCreate,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services import workspace as workspace_service

_ALREADY_HAS_ACCESS = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="This user already has access to the workspace",
)

_WORKSPACE_MEMBER_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace member not found",
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


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_workspace_member(
    workspace_id: uuid.UUID,
    payload: WorkspaceMemberCreate,
    membership: CanManageWorkspaces,
    db: DbSession,
) -> WorkspaceMemberResponse:
    """
    Grant an existing organization member access to a workspace. Owners and
    admins only.
    """

    try:
        member = await workspace_service.add_member(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            member_id=payload.member_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except MemberNotFound as exc:
        raise _MEMBER_NOT_FOUND from exc
    except WorkspaceMemberAlreadyExists as exc:
        raise _ALREADY_HAS_ACCESS from exc

    await db.commit()

    user = await user_repo.get_by_id(db, member.user_id)

    return WorkspaceMemberResponse(
        id=member.id,
        workspace_id=member.workspace_id,
        created_at=member.created_at,
        user=MemberUserResponse.model_validate(user),
    )


@router.get(
    "/organizations/{organization_id}/workspaces/{workspace_id}/members",
    response_model=list[WorkspaceMemberResponse],
)
async def list_workspace_members(
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[WorkspaceMemberResponse]:
    """
    List everyone with access to the workspace.
    """

    rows = await workspace_service.list_workspace_members(db, workspace.id)

    return [
        WorkspaceMemberResponse(
            id=member.id,
            workspace_id=member.workspace_id,
            created_at=member.created_at,
            user=MemberUserResponse.model_validate(user),
        )
        for member, user in rows
    ]


@router.delete(
    "/organizations/{organization_id}/workspaces/{workspace_id}/members/{workspace_member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_workspace_member(
    workspace_id: uuid.UUID,
    workspace_member_id: uuid.UUID,
    membership: CanManageWorkspaces,
    db: DbSession,
) -> Response:
    """
    Revoke a workspace membership. Owners and admins only.
    """

    try:
        await workspace_service.remove_member(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            workspace_member_id=workspace_member_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except WorkspaceMemberNotFound as exc:
        raise _WORKSPACE_MEMBER_NOT_FOUND from exc

    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
