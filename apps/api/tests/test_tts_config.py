import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.models.assistant import Assistant
from app.models.assistant_version import AssistantVersion
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.services.tts_config import (
    DEFAULT_SPEECH_RATE,
    DEFAULT_VOICE_ID,
    resolve_tts_config,
)


async def _make_assistant(db: AsyncSession, slug: str) -> Assistant:
    organization = Organization(name=slug, slug=slug)
    db.add(organization)
    await db.flush()

    workspace = Workspace(organization_id=organization.id, name="Clinic")
    db.add(workspace)
    await db.flush()

    assistant = Assistant(
        organization_id=organization.id,
        workspace_id=workspace.id,
        name="Test Assistant",
    )
    db.add(assistant)
    await db.flush()

    return assistant


async def _make_version(
    db: AsyncSession, assistant: Assistant, *, voice_id: str, speech_rate: float
) -> AssistantVersion:
    version = AssistantVersion(
        assistant_id=assistant.id,
        version=1,
        voice_id=voice_id,
        language="en",
        greeting="Hello",
        persona=None,
        speech_rate=speech_rate,
        turn_sensitivity=0.5,
        creativity=0.3,
        ambient_sound=None,
    )
    db.add(version)
    await db.flush()

    assistant.current_version_id = version.id
    await db.flush()

    return version


async def test_returns_the_published_versions_voice_and_rate(db: AsyncSession) -> None:
    assistant = await _make_assistant(db, "tts-config-ok")
    await _make_version(db, assistant, voice_id="voice-42", speech_rate=1.4)

    config = await resolve_tts_config(db, assistant.id)

    assert config.voice_id == "voice-42"
    assert config.speech_rate == 1.4


async def test_falls_back_to_the_fixed_defaults_for_an_unpublished_assistant(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "tts-config-unpublished")

    config = await resolve_tts_config(db, assistant.id)

    assert config.voice_id == DEFAULT_VOICE_ID
    assert config.speech_rate == DEFAULT_SPEECH_RATE


async def test_raises_for_an_unknown_assistant(db: AsyncSession) -> None:
    with pytest.raises(AssistantNotFound):
        await resolve_tts_config(db, uuid.uuid4())
