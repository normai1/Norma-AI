import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant_version import AssistantVersion


async def get_by_id(
    db: AsyncSession,
    version_id: uuid.UUID,
) -> AssistantVersion | None:
    """
    Look up an assistant version by primary key - what
    Assistant.current_version_id points at.
    """

    return await db.scalar(
        select(AssistantVersion).where(AssistantVersion.id == version_id),
    )


async def get_by_version(
    db: AsyncSession,
    assistant_id: uuid.UUID,
    version: int,
) -> AssistantVersion | None:
    """
    Look up one version of an assistant by its version number.
    """

    return await db.scalar(
        select(AssistantVersion).where(
            AssistantVersion.assistant_id == assistant_id,
            AssistantVersion.version == version,
        ),
    )


async def list_for_assistant(
    db: AsyncSession,
    assistant_id: uuid.UUID,
) -> list[AssistantVersion]:
    """
    Every version of an assistant, newest first.
    """

    result = await db.scalars(
        select(AssistantVersion)
        .where(AssistantVersion.assistant_id == assistant_id)
        .order_by(AssistantVersion.version.desc()),
    )

    return list(result.all())


async def next_version_number(db: AsyncSession, assistant_id: uuid.UUID) -> int:
    """
    The next version number for an assistant: one past the highest existing
    version, or 1 if it has none yet.
    """

    highest = await db.scalar(
        select(func.max(AssistantVersion.version)).where(
            AssistantVersion.assistant_id == assistant_id,
        ),
    )

    return (highest or 0) + 1


async def create(
    db: AsyncSession,
    *,
    assistant_id: uuid.UUID,
    version: int,
    voice_id: str,
    language: str,
    greeting: str,
    persona: str | None,
    speech_rate: float,
    turn_sensitivity: float,
    creativity: float,
    ambient_sound: str | None,
    prompt_template_id: uuid.UUID | None = None,
    prompt_version: int | None = None,
) -> AssistantVersion:
    """
    Insert a new, immutable version snapshot.
    """

    assistant_version = AssistantVersion(
        assistant_id=assistant_id,
        version=version,
        voice_id=voice_id,
        language=language,
        greeting=greeting,
        persona=persona,
        speech_rate=speech_rate,
        turn_sensitivity=turn_sensitivity,
        creativity=creativity,
        ambient_sound=ambient_sound,
        prompt_template_id=prompt_template_id,
        prompt_version=prompt_version,
    )

    db.add(assistant_version)
    await db.flush()

    return assistant_version
