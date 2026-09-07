import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AssistantArchived,
    AssistantNotFound,
    WorkspaceNotFound,
)
from app.models.assistant import Assistant
from app.repositories import assistant as assistant_repo
from app.repositories import workspace as workspace_repo
from app.services.prompt_rendering import render_prompt

# Mirrors llm_config.py's _resolve_system_prompt exactly - only the
# namespace/field *names* used here matter for validation (a typo'd
# placeholder is what render_prompt rejects), not the actual values a real
# call would substitute, so stand-ins are fine.
_PROMPT_VALIDATION_CONTEXT: dict[str, dict[str, Any]] = {
    "workspace": {"name": ""},
    "assistant": {"name": ""},
    "caller": {"name": None},
}


async def _resolve_workspace_id(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    """
    Confirm the workspace exists in the caller's organization before any
    assistant operation touches it.
    """

    workspace = await workspace_repo.get_by_id(db, workspace_id)

    if workspace is None or workspace.organization_id != organization_id:
        raise WorkspaceNotFound

    return workspace.id


async def resolve_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> Assistant:
    """
    Look up an assistant, refusing one outside the caller's workspace.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None or assistant.workspace_id != workspace_id:
        raise AssistantNotFound

    return assistant


async def create_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
) -> Assistant:
    """
    Create an assistant in a workspace, refusing one outside the caller's
    organization.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    return await assistant_repo.create(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        name=name,
    )


async def list_assistants(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[Assistant]:
    """
    Every assistant in a workspace, refusing one outside the caller's
    organization.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    return await assistant_repo.list_for_workspace(db, workspace_id)


async def get_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> Assistant:
    """
    Fetch one assistant the caller may access.
    """

    return await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )


async def update_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    fields: dict[str, Any],
) -> Assistant:
    """
    Apply a partial update to an assistant the caller may manage - name
    and/or any configuration field, whichever `fields` actually contains.

    A custom_prompt is validated by actually rendering it here, against the
    same namespace/field shape a live call resolves it with (see
    llm_config.py's _resolve_system_prompt). Without this, a typo'd
    placeholder (e.g. {{business.name}} - workspace.name is the real one)
    would save successfully and then silently fall back to the assistant's
    persona, then the fixed generic default, on every real call - with
    nothing telling the operator their own instructions were never actually
    in effect. Raises PromptRenderError, same as a live call's own render
    would, but here it stops the save instead of being swallowed.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    custom_prompt = fields.get("custom_prompt")
    if custom_prompt:
        render_prompt(custom_prompt, _PROMPT_VALIDATION_CONTEXT)

    return await assistant_repo.update(db, assistant, fields=fields)


async def archive_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> Assistant:
    """
    Archive an assistant the caller may manage. Idempotent.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    return await assistant_repo.archive(db, assistant)


async def delete_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> None:
    """
    Permanently delete an assistant the caller may manage. Irreversible -
    unlike archive_assistant, there is no undo. Cascades to everything the
    assistant owns via existing foreign keys.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    await assistant_repo.delete(db, assistant)


async def publish_assistant(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> Assistant:
    """
    Mark an assistant's current configuration as live. Refuses an archived
    assistant: there is no restore path yet, so nothing could legally bring
    it back to life.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    if assistant.status == assistant_repo.ARCHIVED_STATUS:
        raise AssistantArchived

    return await assistant_repo.publish(db, assistant)
