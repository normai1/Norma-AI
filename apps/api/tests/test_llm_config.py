import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.models.assistant import Assistant
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.services.llm_config import DEFAULT_SYSTEM_PROMPT, resolve_llm_config

_DEFAULT_CREATIVITY = 0.3


async def _make_assistant(
    db: AsyncSession,
    slug: str,
    *,
    name: str = "Test Assistant",
    persona: str | None = None,
    creativity: float | None = None,
    custom_prompt: str | None = None,
) -> Assistant:
    organization = Organization(name=slug, slug=slug)
    db.add(organization)
    await db.flush()

    workspace = Workspace(organization_id=organization.id, name="Clinic")
    db.add(workspace)
    await db.flush()

    assistant = Assistant(
        organization_id=organization.id,
        workspace_id=workspace.id,
        name=name,
        persona=persona,
        custom_prompt=custom_prompt,
    )
    if creativity is not None:
        assistant.creativity = creativity
    db.add(assistant)
    await db.flush()

    return assistant


async def test_renders_the_custom_prompt_with_workspace_and_assistant_context(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(
        db,
        "llm-config-render",
        name="Norma",
        creativity=0.7,
        custom_prompt="You are {{assistant.name}} for {{workspace.name}}.",
    )

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == "You are Norma for Clinic."
    assert config.creativity == 0.7


async def test_falls_back_to_persona_when_no_custom_prompt_is_set(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(
        db, "llm-config-no-prompt", persona="Be warm and concise."
    )

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == "Be warm and concise."


async def test_falls_back_to_persona_when_the_custom_prompts_render_fails(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(
        db,
        "llm-config-render-fails",
        persona="Fallback persona.",
        custom_prompt="{{caller.phone_number}}",
    )

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == "Fallback persona."


async def test_falls_back_to_the_fixed_default_when_nothing_is_set(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "llm-config-fixed-default")

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert config.creativity == _DEFAULT_CREATIVITY


async def test_raises_for_an_unknown_assistant(db: AsyncSession) -> None:
    with pytest.raises(AssistantNotFound):
        await resolve_llm_config(db, uuid.uuid4())
