import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.assistant import Assistant
from app.models.organization import Organization
from app.models.workspace import Workspace

_TTS_CONFIG_URL = "/internal/v1/assistants/{assistant_id}/tts-config"


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


async def test_returns_the_assistants_config(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-tts-config-ok")
    assistant.voice_id = "voice-7"
    assistant.speech_rate = 0.8
    await db.flush()

    response = await client.get(
        _TTS_CONFIG_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert response.json() == {"voice_id": "voice-7", "speech_rate": 0.8}


async def test_returns_the_default_config_for_an_unconfigured_assistant(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-tts-config-unconfigured")

    response = await client.get(
        _TTS_CONFIG_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert response.json() == {"voice_id": "default", "speech_rate": 1.0}


async def test_404s_for_an_unknown_assistant(client: AsyncClient) -> None:
    response = await client.get(
        _TTS_CONFIG_URL.format(assistant_id=uuid.uuid4()),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 404


async def test_401s_with_a_missing_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-tts-config-no-header")

    response = await client.get(_TTS_CONFIG_URL.format(assistant_id=assistant.id))

    assert response.status_code == 401


async def test_401s_with_a_wrong_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-tts-config-wrong-header")

    response = await client.get(
        _TTS_CONFIG_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": "definitely-not-the-real-secret"},
    )

    assert response.status_code == 401
