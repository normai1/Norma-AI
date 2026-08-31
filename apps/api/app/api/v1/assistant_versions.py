import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.org_deps import CanManageAssistants
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import (
    AssistantNotFound,
    AssistantVersionNotFound,
    PromptTemplateNotFound,
    PromptVersionNotFound,
    WorkspaceNotFound,
)
from app.schemas.assistant_version import (
    AssistantVersionCreate,
    AssistantVersionDiffResponse,
    AssistantVersionFieldDiff,
    AssistantVersionResponse,
)
from app.services import assistant_version as assistant_version_service

router = APIRouter(tags=["assistant-versions"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)

_ASSISTANT_VERSION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant version not found",
)

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)

_PROMPT_TEMPLATE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Prompt template not found",
)

_PROMPT_VERSION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Prompt version not found",
)

_PREFIX = (
    "/organizations/{organization_id}/workspaces/{workspace_id}"
    "/assistants/{assistant_id}/versions"
)


@router.post(
    _PREFIX,
    response_model=AssistantVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assistant_version(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    payload: AssistantVersionCreate,
    membership: CanManageAssistants,
    db: DbSession,
) -> AssistantVersionResponse:
    """
    Save a new configuration snapshot for an assistant. Owners and admins
    only. Never sets Assistant.current_version_id - that's 11c's /publish.
    """

    try:
        assistant_version = await assistant_version_service.create_version(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            voice_id=payload.voice_id,
            language=payload.language,
            greeting=payload.greeting,
            persona=payload.persona,
            speech_rate=payload.speech_rate,
            turn_sensitivity=payload.turn_sensitivity,
            creativity=payload.creativity,
            ambient_sound=payload.ambient_sound,
            ambient_sound_volume=payload.ambient_sound_volume,
            max_call_duration_seconds=payload.max_call_duration_seconds,
            max_silence_timeout_seconds=payload.max_silence_timeout_seconds,
            record_calls=payload.record_calls,
            auto_delete_on_declined_consent=payload.auto_delete_on_declined_consent,
            prompt_template_id=payload.prompt_template_id,
            prompt_version=payload.prompt_version,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc
    except PromptVersionNotFound as exc:
        raise _PROMPT_VERSION_NOT_FOUND from exc

    await db.commit()

    return AssistantVersionResponse.model_validate(assistant_version)


@router.get(_PREFIX, response_model=list[AssistantVersionResponse])
async def list_assistant_versions(
    assistant_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[AssistantVersionResponse]:
    """
    List an assistant's versions, newest first. Any workspace member may see them.
    """

    try:
        versions = await assistant_version_service.list_versions(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            assistant_id=assistant_id,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return [AssistantVersionResponse.model_validate(version) for version in versions]


@router.get(f"{_PREFIX}/{{version}}", response_model=AssistantVersionResponse)
async def get_assistant_version(
    assistant_id: uuid.UUID,
    version: int,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> AssistantVersionResponse:
    """
    Fetch one version of an assistant. Any workspace member may see it.
    """

    try:
        assistant_version = await assistant_version_service.get_version(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            assistant_id=assistant_id,
            version=version,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except AssistantVersionNotFound as exc:
        raise _ASSISTANT_VERSION_NOT_FOUND from exc

    return AssistantVersionResponse.model_validate(assistant_version)


@router.get(
    f"{_PREFIX}/{{from_version}}/diff/{{to_version}}",
    response_model=AssistantVersionDiffResponse,
)
async def diff_assistant_versions(
    assistant_id: uuid.UUID,
    from_version: int,
    to_version: int,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> AssistantVersionDiffResponse:
    """
    The config fields that differ between two versions. Any workspace
    member may see it.
    """

    try:
        changes = await assistant_version_service.diff_versions(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            assistant_id=assistant_id,
            from_version=from_version,
            to_version=to_version,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except AssistantVersionNotFound as exc:
        raise _ASSISTANT_VERSION_NOT_FOUND from exc

    return AssistantVersionDiffResponse(
        from_version=from_version,
        to_version=to_version,
        changes={
            field: AssistantVersionFieldDiff(previous=previous, current=current)
            for field, (previous, current) in changes.items()
        },
    )
