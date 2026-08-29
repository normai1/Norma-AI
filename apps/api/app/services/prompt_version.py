import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PromptVersionNotFound
from app.models.prompt_version import PromptVersion
from app.repositories import prompt_version as prompt_version_repo
from app.services.prompt_template import resolve_prompt_template

# The one diffable field a PromptVersion has - there is nothing else on it
# meaningfully "what changed" to an operator.
_DIFFABLE_FIELDS = ("content",)


async def create_version(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    content: str,
) -> PromptVersion:
    """
    Save a new, immutable content snapshot for a prompt template, refusing
    one outside the caller's workspace.
    """

    prompt_template = await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )

    version = await prompt_version_repo.next_version_number(db, prompt_template.id)

    return await prompt_version_repo.create(
        db,
        prompt_template_id=prompt_template.id,
        version=version,
        content=content,
    )


async def list_versions(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
) -> list[PromptVersion]:
    """
    Every version of a prompt template, refusing one outside the caller's
    workspace.
    """

    prompt_template = await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )

    return await prompt_version_repo.list_for_template(db, prompt_template.id)


async def get_version(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    version: int,
) -> PromptVersion:
    """
    Fetch one version of a prompt template the caller may access.
    """

    prompt_template = await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )

    prompt_version = await prompt_version_repo.get_by_version(
        db,
        prompt_template.id,
        version,
    )

    if prompt_version is None:
        raise PromptVersionNotFound

    return prompt_version


async def diff_versions(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    from_version: int,
    to_version: int,
) -> dict[str, tuple[Any, Any]]:
    """
    The fields that differ between two versions of a prompt template,
    refusing either version outside the caller's workspace. Only fields
    that actually differ are included.
    """

    prompt_template = await resolve_prompt_template(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        prompt_template_id=prompt_template_id,
    )

    older = await prompt_version_repo.get_by_version(
        db, prompt_template.id, from_version
    )
    newer = await prompt_version_repo.get_by_version(db, prompt_template.id, to_version)

    if older is None or newer is None:
        raise PromptVersionNotFound

    return {
        field: (getattr(older, field), getattr(newer, field))
        for field in _DIFFABLE_FIELDS
        if getattr(older, field) != getattr(newer, field)
    }
