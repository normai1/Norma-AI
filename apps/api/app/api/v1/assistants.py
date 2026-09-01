import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import DbSession
from app.api.org_deps import CanManageAssistants
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import (
    AssistantArchived,
    AssistantNotFound,
    WorkspaceNotFound,
)
from app.schemas.assistant import AssistantCreate, AssistantResponse, AssistantUpdate
from app.schemas.voice_session import VoiceSessionTicketResponse
from app.services import assistant as assistant_service
from app.services import voice_session as voice_session_service

router = APIRouter(tags=["assistants"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)

_ASSISTANT_ARCHIVED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="This assistant is archived and cannot be published",
)

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}/assistants",
    response_model=AssistantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assistant(
    workspace_id: uuid.UUID,
    payload: AssistantCreate,
    membership: CanManageAssistants,
    db: DbSession,
) -> AssistantResponse:
    """
    Create an assistant in a workspace. Owners and admins only.
    """

    try:
        assistant = await assistant_service.create_assistant(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            name=payload.name,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc

    await db.commit()

    return AssistantResponse.model_validate(assistant)


@router.get(
    "/organizations/{organization_id}/workspaces/{workspace_id}/assistants",
    response_model=list[AssistantResponse],
)
async def list_assistants(
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[AssistantResponse]:
    """
    List assistants in a workspace. Any workspace member may see them.
    """

    assistants = await assistant_service.list_assistants(
        db,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
    )

    return [AssistantResponse.model_validate(assistant) for assistant in assistants]


@router.get(
    "/organizations/{organization_id}/workspaces/{workspace_id}/assistants/{assistant_id}",
    response_model=AssistantResponse,
)
async def get_assistant(
    assistant_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> AssistantResponse:
    """
    Fetch one assistant. Any workspace member may see it.
    """

    try:
        assistant = await assistant_service.get_assistant(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            assistant_id=assistant_id,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return AssistantResponse.model_validate(assistant)


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}"
    "/assistants/{assistant_id}/test-call-token",
    response_model=VoiceSessionTicketResponse,
)
async def issue_test_call_token(
    assistant_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> VoiceSessionTicketResponse:
    """
    Issue a short-lived ticket authorizing a browser test call against this
    assistant. Any workspace member may request one, matching
    get_assistant's own "any member may see it" precedent.
    """

    try:
        ticket, expires_in = await voice_session_service.issue_test_call_ticket(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            assistant_id=assistant_id,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return VoiceSessionTicketResponse(ticket=ticket, expires_in=expires_in)


@router.patch(
    "/organizations/{organization_id}/workspaces/{workspace_id}/assistants/{assistant_id}",
    response_model=AssistantResponse,
)
async def update_assistant(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    payload: AssistantUpdate,
    membership: CanManageAssistants,
    db: DbSession,
) -> AssistantResponse:
    """
    Update an assistant in place - name and/or any configuration field,
    whichever the request actually includes. Owners and admins only.
    """

    try:
        assistant = await assistant_service.update_assistant(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            fields=payload.model_dump(exclude_unset=True),
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    await db.commit()

    return AssistantResponse.model_validate(assistant)


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}"
    "/assistants/{assistant_id}/publish",
    response_model=AssistantResponse,
)
async def publish_assistant(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    membership: CanManageAssistants,
    db: DbSession,
) -> AssistantResponse:
    """
    Mark an assistant's current configuration as live. Owners and admins
    only.
    """

    try:
        assistant = await assistant_service.publish_assistant(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except AssistantArchived as exc:
        raise _ASSISTANT_ARCHIVED from exc

    await db.commit()

    return AssistantResponse.model_validate(assistant)


@router.delete(
    "/organizations/{organization_id}/workspaces/{workspace_id}/assistants/{assistant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_assistant(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    membership: CanManageAssistants,
    db: DbSession,
) -> Response:
    """
    Permanently delete an assistant. Owners and admins only. Irreversible -
    cascades to its knowledge sources, chunks, and glossary entries. Use
    archive instead for a reversible option.
    """

    try:
        await assistant_service.delete_assistant(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}/assistants/{assistant_id}/archive",
    response_model=AssistantResponse,
)
async def archive_assistant(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    membership: CanManageAssistants,
    db: DbSession,
) -> AssistantResponse:
    """
    Archive an assistant. Owners and admins only. Idempotent.
    """

    try:
        assistant = await assistant_service.archive_assistant(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    await db.commit()

    return AssistantResponse.model_validate(assistant)
