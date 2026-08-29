import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantVersionNotFound
from app.models.assistant_version import AssistantVersion
from app.repositories import assistant_version as assistant_version_repo
from app.services.assistant import resolve_assistant

# The eight config fields 11b defined - the only ones a version "diff" means
# anything for. id/assistant_id/version/timestamps are never meaningfully
# "what changed" information to an operator.
_DIFFABLE_FIELDS = (
    "voice_id",
    "language",
    "greeting",
    "persona",
    "speech_rate",
    "turn_sensitivity",
    "creativity",
    "ambient_sound",
)


async def create_version(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    voice_id: str,
    language: str,
    greeting: str,
    persona: str | None,
    speech_rate: float,
    turn_sensitivity: float,
    creativity: float,
    ambient_sound: str | None,
) -> AssistantVersion:
    """
    Save a new, immutable configuration snapshot for an assistant, refusing
    one outside the caller's workspace.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    version = await assistant_version_repo.next_version_number(db, assistant.id)

    return await assistant_version_repo.create(
        db,
        assistant_id=assistant.id,
        version=version,
        voice_id=voice_id,
        language=language,
        greeting=greeting,
        persona=persona,
        speech_rate=speech_rate,
        turn_sensitivity=turn_sensitivity,
        creativity=creativity,
        ambient_sound=ambient_sound,
    )


async def list_versions(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> list[AssistantVersion]:
    """
    Every version of an assistant, refusing one outside the caller's workspace.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    return await assistant_version_repo.list_for_assistant(db, assistant.id)


async def get_version(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    version: int,
) -> AssistantVersion:
    """
    Fetch one version of an assistant the caller may access.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    assistant_version = await assistant_version_repo.get_by_version(
        db,
        assistant.id,
        version,
    )

    if assistant_version is None:
        raise AssistantVersionNotFound

    return assistant_version


async def diff_versions(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    from_version: int,
    to_version: int,
) -> dict[str, tuple[Any, Any]]:
    """
    The config fields that differ between two versions of an assistant,
    refusing either version outside the caller's workspace. Only fields
    that actually differ are included.
    """

    assistant = await resolve_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    older = await assistant_version_repo.get_by_version(db, assistant.id, from_version)
    newer = await assistant_version_repo.get_by_version(db, assistant.id, to_version)

    if older is None or newer is None:
        raise AssistantVersionNotFound

    return {
        field: (getattr(older, field), getattr(newer, field))
        for field in _DIFFABLE_FIELDS
        if getattr(older, field) != getattr(newer, field)
    }
