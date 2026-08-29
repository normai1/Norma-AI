import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.org_deps import CanManagePromptTemplates
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import (
    PromptTemplateArchived,
    PromptTemplateNotFound,
    PromptVersionNotFound,
    WorkspaceNotFound,
)
from app.schemas.prompt_template import (
    PromptTemplateCreate,
    PromptTemplatePublish,
    PromptTemplateResponse,
    PromptTemplateUpdate,
)
from app.services import prompt_template as prompt_template_service

router = APIRouter(tags=["prompt-templates"])

_PROMPT_TEMPLATE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Prompt template not found",
)

_PROMPT_VERSION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Prompt version not found",
)

_PROMPT_TEMPLATE_ARCHIVED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="This prompt template is archived and cannot be published",
)

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}/prompt-templates",
    response_model=PromptTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_template(
    workspace_id: uuid.UUID,
    payload: PromptTemplateCreate,
    membership: CanManagePromptTemplates,
    db: DbSession,
) -> PromptTemplateResponse:
    """
    Create a prompt template in a workspace. Owners and admins only.
    """

    try:
        prompt_template = await prompt_template_service.create_prompt_template(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            name=payload.name,
            use_case=payload.use_case,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc

    await db.commit()

    return PromptTemplateResponse.model_validate(prompt_template)


@router.get(
    "/organizations/{organization_id}/workspaces/{workspace_id}/prompt-templates",
    response_model=list[PromptTemplateResponse],
)
async def list_prompt_templates(
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[PromptTemplateResponse]:
    """
    List prompt templates in a workspace. Any workspace member may see them.
    """

    prompt_templates = await prompt_template_service.list_prompt_templates(
        db,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
    )

    return [
        PromptTemplateResponse.model_validate(prompt_template)
        for prompt_template in prompt_templates
    ]


@router.get(
    "/organizations/{organization_id}/workspaces/{workspace_id}/prompt-templates/{prompt_template_id}",
    response_model=PromptTemplateResponse,
)
async def get_prompt_template(
    prompt_template_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> PromptTemplateResponse:
    """
    Fetch one prompt template. Any workspace member may see it.
    """

    try:
        prompt_template = await prompt_template_service.get_prompt_template(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            prompt_template_id=prompt_template_id,
        )
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc

    return PromptTemplateResponse.model_validate(prompt_template)


@router.patch(
    "/organizations/{organization_id}/workspaces/{workspace_id}/prompt-templates/{prompt_template_id}",
    response_model=PromptTemplateResponse,
)
async def rename_prompt_template(
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    payload: PromptTemplateUpdate,
    membership: CanManagePromptTemplates,
    db: DbSession,
) -> PromptTemplateResponse:
    """
    Rename a prompt template. Owners and admins only.
    """

    try:
        prompt_template = await prompt_template_service.rename_prompt_template(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            prompt_template_id=prompt_template_id,
            name=payload.name,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc

    await db.commit()

    return PromptTemplateResponse.model_validate(prompt_template)


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}"
    "/prompt-templates/{prompt_template_id}/publish",
    response_model=PromptTemplateResponse,
)
async def publish_prompt_template(
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    payload: PromptTemplatePublish,
    membership: CanManagePromptTemplates,
    db: DbSession,
) -> PromptTemplateResponse:
    """
    Publish (or roll back to) a version of a prompt template. Owners and
    admins only.
    """

    try:
        prompt_template = await prompt_template_service.publish_prompt_template(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            prompt_template_id=prompt_template_id,
            version=payload.version,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc
    except PromptTemplateArchived as exc:
        raise _PROMPT_TEMPLATE_ARCHIVED from exc
    except PromptVersionNotFound as exc:
        raise _PROMPT_VERSION_NOT_FOUND from exc

    await db.commit()

    return PromptTemplateResponse.model_validate(prompt_template)


@router.post(
    "/organizations/{organization_id}/workspaces/{workspace_id}/prompt-templates/{prompt_template_id}/archive",
    response_model=PromptTemplateResponse,
)
async def archive_prompt_template(
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    membership: CanManagePromptTemplates,
    db: DbSession,
) -> PromptTemplateResponse:
    """
    Archive a prompt template. Owners and admins only. Idempotent.
    """

    try:
        prompt_template = await prompt_template_service.archive_prompt_template(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            prompt_template_id=prompt_template_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except PromptTemplateNotFound as exc:
        raise _PROMPT_TEMPLATE_NOT_FOUND from exc

    await db.commit()

    return PromptTemplateResponse.model_validate(prompt_template)
