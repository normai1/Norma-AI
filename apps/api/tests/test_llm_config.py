import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.models.assistant import Assistant
from app.models.assistant_version import AssistantVersion
from app.models.organization import Organization
from app.models.prompt_template import PromptTemplate
from app.models.prompt_version import PromptVersion
from app.models.workspace import Workspace
from app.services.llm_config import (
    DEFAULT_CREATIVITY,
    DEFAULT_SYSTEM_PROMPT,
    resolve_llm_config,
)


async def _make_assistant(
    db: AsyncSession, slug: str, *, name: str = "Test Assistant"
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
    )
    db.add(assistant)
    await db.flush()

    return assistant


async def _make_version(
    db: AsyncSession,
    assistant: Assistant,
    *,
    persona: str | None = None,
    creativity: float = 0.6,
    prompt_template_id: uuid.UUID | None = None,
    prompt_version: int | None = None,
) -> AssistantVersion:
    version = AssistantVersion(
        assistant_id=assistant.id,
        version=1,
        voice_id="voice-1",
        language="en",
        greeting="Hello",
        persona=persona,
        speech_rate=1.0,
        turn_sensitivity=0.5,
        creativity=creativity,
        ambient_sound=None,
        prompt_template_id=prompt_template_id,
        prompt_version=prompt_version,
    )
    db.add(version)
    await db.flush()

    assistant.current_version_id = version.id
    await db.flush()

    return version


async def _make_prompt_template_version(
    db: AsyncSession, assistant: Assistant, *, content: str
) -> tuple[uuid.UUID, int]:
    template = PromptTemplate(
        organization_id=assistant.organization_id,
        workspace_id=assistant.workspace_id,
        name="Receptionist",
        use_case="receptionist",
    )
    db.add(template)
    await db.flush()

    prompt_version = PromptVersion(
        prompt_template_id=template.id, version=1, content=content
    )
    db.add(prompt_version)
    await db.flush()

    return template.id, prompt_version.version


async def test_renders_the_assigned_template_with_workspace_and_assistant_context(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "llm-config-render", name="Norma")
    template_id, version_number = await _make_prompt_template_version(
        db, assistant, content="You are {{assistant.name}} for {{workspace.name}}."
    )
    await _make_version(
        db,
        assistant,
        creativity=0.7,
        prompt_template_id=template_id,
        prompt_version=version_number,
    )

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == "You are Norma for Clinic."
    assert config.creativity == 0.7


async def test_falls_back_to_persona_when_no_template_is_assigned(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "llm-config-no-template")
    await _make_version(db, assistant, persona="Be warm and concise.")

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == "Be warm and concise."


async def test_falls_back_to_persona_when_the_assigned_templates_render_fails(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "llm-config-render-fails")
    template_id, version_number = await _make_prompt_template_version(
        db, assistant, content="{{caller.phone_number}}"
    )
    await _make_version(
        db,
        assistant,
        persona="Fallback persona.",
        prompt_template_id=template_id,
        prompt_version=version_number,
    )

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == "Fallback persona."


async def test_falls_back_to_the_fixed_default_when_persona_and_template_are_both_unset(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "llm-config-fixed-default")
    await _make_version(db, assistant, persona=None)

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == DEFAULT_SYSTEM_PROMPT


async def test_falls_back_to_the_fixed_defaults_for_an_unpublished_assistant(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "llm-config-unpublished")

    config = await resolve_llm_config(db, assistant.id)

    assert config.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert config.creativity == DEFAULT_CREATIVITY


async def test_raises_for_an_unknown_assistant(db: AsyncSession) -> None:
    with pytest.raises(AssistantNotFound):
        await resolve_llm_config(db, uuid.uuid4())
