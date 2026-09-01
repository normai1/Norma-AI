import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant

ARCHIVED_STATUS = "archived"
PUBLISHED_STATUS = "published"

# The fields an operator may edit via AssistantUpdate - everything except
# identity (id/organization_id/workspace_id) and lifecycle (status), which
# have their own dedicated operations (archive/publish/delete).
_UPDATABLE_FIELDS = frozenset(
    {
        "name",
        "voice_id",
        "language",
        "greeting",
        "persona",
        "custom_prompt",
        "speech_rate",
        "turn_sensitivity",
        "creativity",
        "ambient_sound",
        "ambient_sound_volume",
        "max_call_duration_seconds",
        "max_silence_timeout_seconds",
        "record_calls",
        "auto_delete_on_declined_consent",
    }
)


async def get_by_id(db: AsyncSession, assistant_id: uuid.UUID) -> Assistant | None:
    """
    Look up an assistant by primary key.
    """

    return await db.scalar(select(Assistant).where(Assistant.id == assistant_id))


async def list_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[Assistant]:
    """
    Every assistant in a workspace.
    """

    result = await db.scalars(
        select(Assistant)
        .where(Assistant.workspace_id == workspace_id)
        .order_by(Assistant.created_at),
    )

    return list(result.all())


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
) -> Assistant:
    """
    Insert a new assistant, in the default 'draft' status. voice_id/
    greeting stay unset until the operator configures them; speech_rate/
    turn_sensitivity/creativity get their DB-level defaults immediately.
    """

    assistant = Assistant(
        organization_id=organization_id,
        workspace_id=workspace_id,
        name=name,
    )

    db.add(assistant)
    await db.flush()

    return assistant


async def update(
    db: AsyncSession,
    assistant: Assistant,
    *,
    fields: dict[str, Any],
) -> Assistant:
    """
    Apply a partial update in place - only the keys present in `fields` are
    changed. `fields` must already be limited to _UPDATABLE_FIELDS by the
    caller (AssistantUpdate.model_dump(exclude_unset=True) naturally is).
    """

    for key, value in fields.items():
        if key not in _UPDATABLE_FIELDS:
            raise ValueError(f"{key!r} is not an updatable Assistant field")

        setattr(assistant, key, value)

    await db.flush()

    return assistant


async def archive(db: AsyncSession, assistant: Assistant) -> Assistant:
    """
    Move an assistant to the 'archived' status. Idempotent: archiving an
    already-archived assistant is a no-op rather than an error.
    """

    assistant.status = ARCHIVED_STATUS

    await db.flush()

    return assistant


async def delete(db: AsyncSession, assistant: Assistant) -> None:
    """
    Permanently remove an assistant. Cascades (via existing ondelete="CASCADE"
    foreign keys) to its KnowledgeSource, Chunk, and GlossaryEntry rows.
    """

    await db.delete(assistant)
    await db.flush()


async def publish(db: AsyncSession, assistant: Assistant) -> Assistant:
    """
    Move an assistant to 'published'. A pure status flip now - there is no
    version to point at, so "publish" means "this configuration is live",
    not "point at a chosen snapshot." Idempotent: publishing an already-
    published assistant is a no-op.
    """

    assistant.status = PUBLISHED_STATUS

    await db.flush()

    return assistant
