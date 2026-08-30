import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.assistant import Assistant
from app.models.chunk import Chunk
from app.models.knowledge_source import KnowledgeSource
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.providers.mock_embedding import MockEmbeddingProvider

_RETRIEVE_URL = "/internal/v1/assistants/{assistant_id}/retrieve"


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

    return assistant, organization, workspace


async def test_returns_a_matching_chunk_in_the_context_string(
    client: AsyncClient, db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    assistant, organization, workspace = await _make_assistant(
        db, "internal-retrieve-ok"
    )
    source = KnowledgeSource(
        organization_id=organization.id, workspace_id=workspace.id, type="file"
    )
    db.add(source)
    await db.flush()

    query = "What are your business hours?"
    [vector] = await embedding_provider.embed([query])
    db.add(
        Chunk(
            organization_id=organization.id,
            workspace_id=workspace.id,
            knowledge_source_id=source.id,
            text=query,
            ordering=0,
            chunk_metadata={},
            embedding=vector,
        )
    )
    await db.flush()

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": query},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert query in response.json()["context"]


async def test_returns_empty_context_when_nothing_matches(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-retrieve-empty"
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": "anything"},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 200
    assert response.json() == {"context": ""}


async def test_404s_for_an_unknown_assistant(client: AsyncClient) -> None:
    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=uuid.uuid4()),
        json={"query": "anything"},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 404


async def test_422s_for_an_empty_query(client: AsyncClient, db: AsyncSession) -> None:
    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-retrieve-invalid"
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": ""},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    assert response.status_code == 422


async def test_401s_with_a_missing_secret_header(
    client: AsyncClient, db: AsyncSession
) -> None:
    assistant, _organization, _workspace = await _make_assistant(
        db, "internal-retrieve-no-header"
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id), json={"query": "anything"}
    )

    assert response.status_code == 401
