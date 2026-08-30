import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.assistant import Assistant
from app.models.glossary_entry import GlossaryEntry
from app.models.organization import Organization
from app.models.workspace import Workspace

_GLOSSARY_URL = "/internal/v1/assistants/{assistant_id}/glossary"


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


async def test_returns_the_assistants_glossary_terms(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-glossary-ok")
    db.add(
        GlossaryEntry(
            organization_id=assistant.organization_id,
            workspace_id=assistant.workspace_id,
            assistant_id=assistant.id,
            term="tinnitus",
        )
    )
    db.add(
        GlossaryEntry(
            organization_id=assistant.organization_id,
            workspace_id=assistant.workspace_id,
            assistant_id=assistant.id,
            term="otoscopy",
        )
    )
    await db.flush()

    response = await client.get(
        _GLOSSARY_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert set(response.json()["terms"]) == {"tinnitus", "otoscopy"}


async def test_returns_an_empty_list_for_an_assistant_with_no_glossary(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-glossary-empty")

    response = await client.get(
        _GLOSSARY_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert response.json()["terms"] == []


async def test_404s_for_an_unknown_assistant(client: AsyncClient) -> None:
    response = await client.get(
        _GLOSSARY_URL.format(assistant_id=uuid.uuid4()),
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 404


async def test_401s_with_a_missing_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-glossary-no-header")

    response = await client.get(_GLOSSARY_URL.format(assistant_id=assistant.id))

    assert response.status_code == 401


async def test_401s_with_a_wrong_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant = await _make_assistant(db, "internal-glossary-wrong-header")

    response = await client.get(
        _GLOSSARY_URL.format(assistant_id=assistant.id),
        headers={"X-Internal-Secret": "definitely-not-the-real-secret"},
    )

    assert response.status_code == 401
