import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_version import PromptVersion


async def get_by_version(
    db: AsyncSession,
    prompt_template_id: uuid.UUID,
    version: int,
) -> PromptVersion | None:
    """
    Look up one version of a prompt template by its version number.
    """

    return await db.scalar(
        select(PromptVersion).where(
            PromptVersion.prompt_template_id == prompt_template_id,
            PromptVersion.version == version,
        ),
    )


async def list_for_template(
    db: AsyncSession,
    prompt_template_id: uuid.UUID,
) -> list[PromptVersion]:
    """
    Every version of a prompt template, newest first.
    """

    result = await db.scalars(
        select(PromptVersion)
        .where(PromptVersion.prompt_template_id == prompt_template_id)
        .order_by(PromptVersion.version.desc()),
    )

    return list(result.all())


async def next_version_number(db: AsyncSession, prompt_template_id: uuid.UUID) -> int:
    """
    The next version number for a prompt template - 1 for the first version,
    otherwise one past the current maximum.
    """

    highest = await db.scalar(
        select(func.max(PromptVersion.version)).where(
            PromptVersion.prompt_template_id == prompt_template_id,
        ),
    )

    return (highest or 0) + 1


async def create(
    db: AsyncSession,
    *,
    prompt_template_id: uuid.UUID,
    version: int,
    content: str,
) -> PromptVersion:
    """
    Insert a new, immutable prompt version.
    """

    prompt_version = PromptVersion(
        prompt_template_id=prompt_template_id,
        version=version,
        content=content,
    )

    db.add(prompt_version)
    await db.flush()

    return prompt_version
