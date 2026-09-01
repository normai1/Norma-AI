import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.models.assistant import Assistant
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.services.tts_config import DEFAULT_VOICE_ID, resolve_tts_config

_DEFAULT_SPEECH_RATE = 1.0


async def _make_assistant(
    db: AsyncSession,
    slug: str,
    *,
    voice_id: str | None = None,
    speech_rate: float | None = None,
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
        name="Test Assistant",
        voice_id=voice_id,
    )
    if speech_rate is not None:
        assistant.speech_rate = speech_rate
    db.add(assistant)
    await db.flush()

    return assistant


async def test_returns_the_assistants_voice_and_rate(db: AsyncSession) -> None:
    assistant = await _make_assistant(
        db, "tts-config-ok", voice_id="voice-42", speech_rate=1.4
    )

    config = await resolve_tts_config(db, assistant.id)

    assert config.voice_id == "voice-42"
    assert config.speech_rate == 1.4


async def test_falls_back_to_the_fixed_defaults_for_an_unconfigured_assistant(
    db: AsyncSession,
) -> None:
    assistant = await _make_assistant(db, "tts-config-unconfigured")

    config = await resolve_tts_config(db, assistant.id)

    assert config.voice_id == DEFAULT_VOICE_ID
    assert config.speech_rate == _DEFAULT_SPEECH_RATE


async def test_raises_for_an_unknown_assistant(db: AsyncSession) -> None:
    with pytest.raises(AssistantNotFound):
        await resolve_tts_config(db, uuid.uuid4())
