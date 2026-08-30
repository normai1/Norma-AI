import time
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.models.assistant import Assistant
from app.models.chunk import Chunk
from app.models.knowledge_source import KnowledgeSource
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.providers.mock_embedding import MockEmbeddingProvider
from app.services.context_builder import build_context
from app.services.retrieval import retrieve


async def _make_org_workspace_assistant(
    db: AsyncSession, slug: str
) -> tuple[Organization, Workspace, Assistant]:
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

    return organization, workspace, assistant


async def _make_knowledge_source(
    db: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    *,
    source_type: str = "file",
) -> KnowledgeSource:
    source = KnowledgeSource(
        organization_id=organization.id,
        workspace_id=workspace.id,
        type=source_type,
    )
    db.add(source)
    await db.flush()

    return source


async def _make_chunk(
    db: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    source: KnowledgeSource,
    *,
    text: str,
    embedding: list[float] | None,
    ordering: int = 0,
    metadata: dict | None = None,
) -> Chunk:
    chunk = Chunk(
        organization_id=organization.id,
        workspace_id=workspace.id,
        knowledge_source_id=source.id,
        text=text,
        ordering=ordering,
        chunk_metadata=metadata or {},
        embedding=embedding,
    )
    db.add(chunk)
    await db.flush()

    return chunk


async def test_retrieve_ranks_the_exact_text_match_first(db: AsyncSession) -> None:
    # MockEmbeddingProvider's vectors are hash-seeded per exact string with
    # no semantic relationship between similar text - so ordering is tested
    # by giving one chunk the query's own text (a provably zero cosine
    # distance), not by relying on natural-language similarity the mock
    # does not model.
    organization, workspace, assistant = await _make_org_workspace_assistant(
        db, "retrieve-order"
    )
    source = await _make_knowledge_source(db, organization, workspace)
    provider = MockEmbeddingProvider()

    query = "What are your business hours?"
    [exact_vector] = await provider.embed([query])
    [other_vector_1] = await provider.embed(["Completely unrelated text one."])
    [other_vector_2] = await provider.embed(["Completely unrelated text two."])

    exact_chunk = await _make_chunk(
        db, organization, workspace, source, text=query, embedding=exact_vector
    )
    await _make_chunk(
        db,
        organization,
        workspace,
        source,
        text="Completely unrelated text one.",
        embedding=other_vector_1,
        ordering=1,
    )
    await _make_chunk(
        db,
        organization,
        workspace,
        source,
        text="Completely unrelated text two.",
        embedding=other_vector_2,
        ordering=2,
    )

    results = await retrieve(
        db,
        provider,
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        query=query,
    )

    assert results[0].chunk_id == exact_chunk.id
    assert results[0].score == pytest.approx(1.0, abs=1e-6)


async def test_retrieve_excludes_a_sibling_organizations_chunks(
    db: AsyncSession,
) -> None:
    organization, workspace, assistant = await _make_org_workspace_assistant(
        db, "retrieve-tenant-a"
    )
    source = await _make_knowledge_source(db, organization, workspace)
    provider = MockEmbeddingProvider()
    [vector] = await provider.embed(["Our own chunk."])
    await _make_chunk(
        db, organization, workspace, source, text="Our own chunk.", embedding=vector
    )

    other_org, other_workspace, _other_assistant = await _make_org_workspace_assistant(
        db, "retrieve-tenant-b"
    )
    other_source = await _make_knowledge_source(db, other_org, other_workspace)
    [other_vector] = await provider.embed(["Someone else's chunk."])
    await _make_chunk(
        db,
        other_org,
        other_workspace,
        other_source,
        text="Someone else's chunk.",
        embedding=other_vector,
    )

    results = await retrieve(
        db,
        provider,
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        query="anything",
    )

    assert all(r.text != "Someone else's chunk." for r in results)


async def test_retrieve_excludes_chunks_with_no_embedding(db: AsyncSession) -> None:
    organization, workspace, assistant = await _make_org_workspace_assistant(
        db, "retrieve-null-embedding"
    )
    source = await _make_knowledge_source(db, organization, workspace)
    provider = MockEmbeddingProvider()
    [vector] = await provider.embed(["Embedded chunk."])
    await _make_chunk(
        db, organization, workspace, source, text="Embedded chunk.", embedding=vector
    )
    await _make_chunk(
        db,
        organization,
        workspace,
        source,
        text="Never embedded.",
        embedding=None,
        ordering=1,
    )

    results = await retrieve(
        db,
        provider,
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        query="anything",
    )

    assert len(results) == 1
    assert results[0].text == "Embedded chunk."


async def test_retrieve_respects_top_k(db: AsyncSession) -> None:
    organization, workspace, assistant = await _make_org_workspace_assistant(
        db, "retrieve-top-k"
    )
    source = await _make_knowledge_source(db, organization, workspace)
    provider = MockEmbeddingProvider()
    for i in range(5):
        [vector] = await provider.embed([f"Chunk number {i}."])
        await _make_chunk(
            db,
            organization,
            workspace,
            source,
            text=f"Chunk number {i}.",
            embedding=vector,
            ordering=i,
        )

    results = await retrieve(
        db,
        provider,
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        query="anything",
        top_k=2,
    )

    assert len(results) == 2


async def test_retrieve_raises_for_an_assistant_in_a_different_workspace(
    db: AsyncSession,
) -> None:
    organization, workspace, assistant = await _make_org_workspace_assistant(
        db, "retrieve-wrong-workspace"
    )
    other_workspace = Workspace(organization_id=organization.id, name="Other")
    db.add(other_workspace)
    await db.flush()

    with pytest.raises(AssistantNotFound):
        await retrieve(
            db,
            MockEmbeddingProvider(),
            organization_id=organization.id,
            workspace_id=other_workspace.id,
            assistant_id=assistant.id,
            query="anything",
        )


async def test_retrieve_raises_for_an_unknown_assistant(db: AsyncSession) -> None:
    organization, workspace, _assistant = await _make_org_workspace_assistant(
        db, "retrieve-unknown-assistant"
    )

    with pytest.raises(AssistantNotFound):
        await retrieve(
            db,
            MockEmbeddingProvider(),
            organization_id=organization.id,
            workspace_id=workspace.id,
            assistant_id=uuid.uuid4(),
            query="anything",
        )


async def test_retrieve_reports_each_chunks_source_type(db: AsyncSession) -> None:
    organization, workspace, assistant = await _make_org_workspace_assistant(
        db, "retrieve-source-type"
    )
    website_source = await _make_knowledge_source(
        db, organization, workspace, source_type="website"
    )
    provider = MockEmbeddingProvider()
    [vector] = await provider.embed(["Website content."])
    await _make_chunk(
        db,
        organization,
        workspace,
        website_source,
        text="Website content.",
        embedding=vector,
    )

    results = await retrieve(
        db,
        provider,
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        query="anything",
    )

    assert results[0].source_type == "website"


async def test_retrieve_and_build_context_latency_regression(
    db: AsyncSession,
) -> None:
    """
    A local regression guard against an obvious slowdown (an N+1 query, a
    missing filter forcing a full scan) - not item 61's real p95
    measurement, which requires production topology and a real embedding
    provider, neither of which exist in this test.
    """

    organization, workspace, assistant = await _make_org_workspace_assistant(
        db, "retrieve-latency"
    )
    source = await _make_knowledge_source(db, organization, workspace)
    provider = MockEmbeddingProvider()
    for i in range(20):
        [vector] = await provider.embed([f"Chunk number {i} with some body text."])
        await _make_chunk(
            db,
            organization,
            workspace,
            source,
            text=f"Chunk number {i} with some body text.",
            embedding=vector,
            ordering=i,
        )

    started_at = time.perf_counter()
    results = await retrieve(
        db,
        provider,
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        query="anything",
    )
    build_context(results)
    elapsed_ms = (time.perf_counter() - started_at) * 1000

    assert elapsed_ms < 500
