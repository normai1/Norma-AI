from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_entry import FaqEntry
from app.models.knowledge_source import KnowledgeSource
from app.providers.llm import LLMProviderUnavailable
from app.providers.mock_llm import MockLLMProvider
from app.providers.mock_web_crawler import MockPageFetcher
from app.services.faq_generation import GENERATED_FAQ_SOURCE_NAME
from tests.conftest import _org_with_owner

ORGS = "/api/v1/organizations"

_GENERATED_PAIRS_JSON = (
    '[{"question": "What are your hours?", "answer": "9am to 5pm."}, '
    '{"question": "Are you open weekends?", "answer": "No, closed weekends."}]'
)


def _workspaces_url(organization_id: str) -> str:
    return f"{ORGS}/{organization_id}/workspaces"


def _knowledge_sources_url(organization_id: str, workspace_id: str) -> str:
    return f"{_workspaces_url(organization_id)}/{workspace_id}/knowledge-sources"


async def _setup_org_workspace(client: AsyncClient, prefix: str):
    owner_headers, organization_id = await _org_with_owner(
        client, f"{prefix}@example.com"
    )
    workspace = await client.post(
        _workspaces_url(organization_id), json={"name": "Clinic"}, headers=owner_headers
    )
    workspace_id = workspace.json()["id"]
    assistant = await client.post(
        f"{_workspaces_url(organization_id)}/{workspace_id}/assistants",
        json={"name": "Front Desk"},
        headers=owner_headers,
    )

    return organization_id, workspace_id, assistant.json()["id"], owner_headers


async def _upload(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    assistant_id: str,
    content: bytes = b"We are open Monday to Friday, 9am to 5pm.",
) -> dict:
    response = await client.post(
        _knowledge_sources_url(organization_id, workspace_id),
        files={"file": ("hours.txt", content, "text/plain")},
        data={"assistant_id": assistant_id},
        headers=headers,
    )

    return response.json()


async def _generated_faq_entries(
    db: AsyncSession, assistant_id: str
) -> list[FaqEntry]:
    source = await db.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.assistant_id == assistant_id,
            KnowledgeSource.name == GENERATED_FAQ_SOURCE_NAME,
        )
    )

    if source is None:
        return []

    result = await db.scalars(
        select(FaqEntry).where(FaqEntry.knowledge_source_id == source.id)
    )

    return list(result.all())


async def test_uploading_a_file_generates_faq_entries_from_its_content(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    faq_llm_provider.response = _GENERATED_PAIRS_JSON

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-upload")
    )

    body = await _upload(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )
    assert body["status"] == "completed"

    entries = await _generated_faq_entries(db, assistant_id)
    questions = {entry.question for entry in entries}

    assert questions == {"What are your hours?", "Are you open weekends?"}
    assert faq_llm_provider.calls  # generate() was actually invoked


async def test_reprocessing_a_file_does_not_generate_duplicate_entries(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    faq_llm_provider.response = _GENERATED_PAIRS_JSON

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-reprocess")
    )

    body = await _upload(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )
    source_id = body["id"]

    entries_after_upload = await _generated_faq_entries(db, assistant_id)
    assert len(entries_after_upload) == 2

    calls_after_upload = len(faq_llm_provider.calls)

    process = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/{source_id}/process",
        headers=owner_headers,
    )
    assert process.json()["status"] == "completed"

    entries_after_reprocess = await _generated_faq_entries(db, assistant_id)

    assert len(entries_after_reprocess) == 2
    assert len(faq_llm_provider.calls) == calls_after_upload


async def test_generation_failure_does_not_fail_the_source(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    faq_llm_provider.failure = LLMProviderUnavailable("simulated outage")

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-fail")
    )

    body = await _upload(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )

    assert body["status"] == "completed"
    assert await _generated_faq_entries(db, assistant_id) == []


async def test_malformed_generation_output_produces_no_faq_entries(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    faq_llm_provider.response = "not valid json at all"

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-malformed")
    )

    body = await _upload(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )

    assert body["status"] == "completed"
    assert await _generated_faq_entries(db, assistant_id) == []


async def test_creating_a_website_source_generates_faq_entries(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
    page_fetcher: MockPageFetcher,
) -> None:
    faq_llm_provider.response = _GENERATED_PAIRS_JSON
    page_fetcher.pages["http://example.com/"] = (
        "<html><body>We are open Monday to Friday, 9am to 5pm.</body></html>"
    )

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-website")
    )

    response = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/website",
        json={"url": "http://example.com/", "assistant_id": assistant_id},
        headers=owner_headers,
    )
    # Registered immediately; the crawl - and the FAQ generation that
    # follows it - run in the background task the request schedules.
    assert response.json()["status"] == "pending"

    source = await client.get(
        f"{_knowledge_sources_url(organization_id, workspace_id)}"
        f"/{response.json()['id']}",
        headers=owner_headers,
    )

    assert source.json()["status"] == "completed"

    entries = await _generated_faq_entries(db, assistant_id)
    questions = {entry.question for entry in entries}

    assert questions == {"What are your hours?", "Are you open weekends?"}


async def test_generated_entries_record_the_source_they_came_from(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    """
    Generated entries are filed under the shared per-assistant container, so
    knowledge_source_id points at that container, not at the document. The
    link back to the document is what makes deletion and attribution
    possible at all.
    """

    faq_llm_provider.response = _GENERATED_PAIRS_JSON

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-origin")
    )

    uploaded = await _upload(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )

    entries = await _generated_faq_entries(db, assistant_id)

    assert entries
    for entry in entries:
        assert str(entry.generated_from_knowledge_source_id) == uploaded["id"]
        # Filed in the shared container, not under the document itself.
        assert str(entry.knowledge_source_id) != uploaded["id"]


async def test_deleting_a_source_deletes_the_faqs_generated_from_it(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    """
    Reported live: an operator deleted a document and its generated FAQs
    stayed behind - still listed, and still embedded and retrievable, so the
    assistant kept answering from a document that no longer existed.
    """

    from app.models.chunk import Chunk

    faq_llm_provider.response = _GENERATED_PAIRS_JSON

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-delete")
    )

    uploaded = await _upload(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )

    entries = await _generated_faq_entries(db, assistant_id)
    assert entries, "precondition: the upload generated FAQ entries"
    entry_ids = [entry.id for entry in entries]

    # Each entry is backed by an embedded chunk, which is what actually
    # reaches a live call.
    chunks_before = await db.scalars(
        select(Chunk).where(
            Chunk.chunk_metadata["faq_entry_id"].astext.in_(
                [str(entry_id) for entry_id in entry_ids]
            )
        )
    )
    assert list(chunks_before), "precondition: the entries were embedded"

    response = await client.delete(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/{uploaded['id']}",
        headers=owner_headers,
    )
    assert response.status_code == 204

    db.expire_all()

    assert await _generated_faq_entries(db, assistant_id) == []

    # The row going is not enough - the embedded copy is what would have
    # gone on answering calls.
    chunks_after = await db.scalars(
        select(Chunk).where(
            Chunk.chunk_metadata["faq_entry_id"].astext.in_(
                [str(entry_id) for entry_id in entry_ids]
            )
        )
    )
    assert list(chunks_after) == []


async def test_deleting_a_source_leaves_operator_written_faqs_alone(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    """
    Only what the document produced goes with it. An operator's own entries
    have no origin recorded and must survive - deleting a document must
    never quietly take their hand-written work with it.
    """

    faq_llm_provider.response = _GENERATED_PAIRS_JSON

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-delete-manual")
    )

    uploaded = await _upload(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )

    manual_source = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/manual-faq",
        json={"name": "Hand written", "assistant_id": assistant_id},
        headers=owner_headers,
    )
    manual_source_id = manual_source.json()["id"]

    created = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}"
        f"/{manual_source_id}/faq-entries",
        json={"question": "Do you validate parking?", "answer": "Yes, for two hours."},
        headers=owner_headers,
    )
    assert created.status_code == 201
    assert created.json()["generated_from_knowledge_source_id"] is None

    response = await client.delete(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/{uploaded['id']}",
        headers=owner_headers,
    )
    assert response.status_code == 204

    surviving = await client.get(
        f"{_knowledge_sources_url(organization_id, workspace_id)}"
        f"/{manual_source_id}/faq-entries",
        headers=owner_headers,
    )

    assert [entry["question"] for entry in surviving.json()] == [
        "Do you validate parking?"
    ]


async def test_the_generated_faq_container_is_not_left_looking_unfinished(
    client: AsyncClient,
    db: AsyncSession,
    faq_llm_provider: MockLLMProvider,
) -> None:
    """
    The container the generated entries land in is created by the generation
    run itself. At the 'pending' default it told the operator their knowledge
    base was still being built while holding the finished questions.
    """

    faq_llm_provider.response = _GENERATED_PAIRS_JSON

    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_org_workspace(client, "faqgen-container-status")
    )

    await _upload(client, organization_id, workspace_id, owner_headers, assistant_id)

    container = await db.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.assistant_id == assistant_id,
            KnowledgeSource.name == GENERATED_FAQ_SOURCE_NAME,
        )
    )

    assert container is not None
    assert container.status == "completed"
