import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.org_deps import CanManagePromptTemplates
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import PromptTemplateNotFound, PromptVersionNotFound
from app.schemas.prompt_version import (
    PromptVersionCreate,
    PromptVersionDiffResponse,
    PromptVersionFieldDiff,
    PromptVersionResponse,
)
from app.services import prompt_version as prompt_version_service

router = APIRouter(tags=["prompt-versions"])

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
    "/prompt-templates/{prompt_template_id}/versions"
)


@router.post(
    _PREFIX,
    response_model=PromptVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_version(
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    payload: PromptVersionCreate,
    membership: CanManagePromptTemplates,
    db: DbSession,
) -> PromptVersionResponse:
    """
    Save a new content snapshot for a prompt template. Owners and admins
    only. Never sets PromptTemplate.current_version_id - that's /publish.
    """

    try:
        prompt_version = await prompt_version_service.create_version(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            prompt_template_id=prompt_template_id,
            content=payload.content,
        )
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc

    await db.commit()

    return PromptVersionResponse.model_validate(prompt_version)


@router.get(_PREFIX, response_model=list[PromptVersionResponse])
async def list_prompt_versions(
    prompt_template_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[PromptVersionResponse]:
    """
    List a prompt template's versions, newest first. Any workspace member
    may see them.
    """

    try:
        prompt_versions = await prompt_version_service.list_versions(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            prompt_template_id=prompt_template_id,
        )
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc

    return [
        PromptVersionResponse.model_validate(prompt_version)
        for prompt_version in prompt_versions
    ]


@router.get(f"{_PREFIX}/{{version}}", response_model=PromptVersionResponse)
async def get_prompt_version(
    prompt_template_id: uuid.UUID,
    version: int,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> PromptVersionResponse:
    """
    Fetch one version of a prompt template. Any workspace member may see it.
    """

    try:
        prompt_version = await prompt_version_service.get_version(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            prompt_template_id=prompt_template_id,
            version=version,
        )
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc
    except PromptVersionNotFound as exc:
        raise _PROMPT_VERSION_NOT_FOUND from exc

    return PromptVersionResponse.model_validate(prompt_version)


@router.get(
    f"{_PREFIX}/{{from_version}}/diff/{{to_version}}",
    response_model=PromptVersionDiffResponse,
)
async def diff_prompt_versions(
    prompt_template_id: uuid.UUID,
    from_version: int,
    to_version: int,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> PromptVersionDiffResponse:
    """
    The fields that differ between two versions of a prompt template. Any
    workspace member may see it.
    """

    try:
        changes = await prompt_version_service.diff_versions(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            prompt_template_id=prompt_template_id,
            from_version=from_version,
            to_version=to_version,
        )
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc
    except PromptVersionNotFound as exc:
        raise _PROMPT_VERSION_NOT_FOUND from exc

    return PromptVersionDiffResponse(
        from_version=from_version,
        to_version=to_version,
        changes={
            field: PromptVersionFieldDiff(previous=previous, current=current)
            for field, (previous, current) in changes.items()
        },
    )
