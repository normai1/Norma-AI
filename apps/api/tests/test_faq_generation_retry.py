"""
A source whose FAQ generation was cut short must be recoverable.

The provider's daily token allowance running out mid-document leaves an
upload that parsed, chunked and embedded perfectly and has no FAQ entries at
all. Generation runs once per source by design - it writes new entries
rather than replacing them, so running it twice files a second near-
identical set - and before this there was no way back to such a source short
of deleting the upload and starting again.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.models.document import Document
from app.models.faq_entry import FaqEntry
from app.models.knowledge_source import KnowledgeSource
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.providers.mock_embedding import MockEmbeddingProvider
from app.services import knowledge_source as knowledge_source_service


class _CountingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1

        return '[{"question": "Q?", "answer": "A."}]'


class _FailingStorage:
    """Nothing here reaches storage - generation should stop before it."""

    async def download(self, key: str) -> bytes:
        raise AssertionError("generation should have stopped before downloading")


async def _make_source(
    db: AsyncSession, slug: str, *, status: str = "failed"
) -> tuple[KnowledgeSource, Assistant]:
    organization = Organization(name=slug, slug=slug)
    db.add(organization)
    await db.flush()

    workspace = Workspace(organization_id=organization.id, name="W")
    db.add(workspace)
    await db.flush()

    assistant = Assistant(
        organization_id=organization.id, workspace_id=workspace.id, name="A"
    )
    db.add(assistant)
    await db.flush()

    source = KnowledgeSource(
        organization_id=organization.id,
        workspace_id=workspace.id,
        assistant_id=assistant.id,
        type="file",
        # A run cut short by the provider's daily limit leaves exactly this:
        # a document that parsed and embedded perfectly, and a source that
        # does not claim to be finished.
        status=status,
    )
    db.add(source)
    await db.flush()

    db.add(
        Document(
            knowledge_source_id=source.id,
            filename="doc.txt",
            storage_key="k",
            content_type="text/plain",
            processing_status="completed",
        )
    )
    await db.flush()

    return source, assistant


async def test_a_source_that_already_has_entries_is_left_alone(
    db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    """
    Generation is not idempotent, so a retry of a source that worked must
    not file a second set of near-identical questions.
    """

    source, assistant = await _make_source(db, "faq-retry-done", status="completed")

    container = KnowledgeSource(
        organization_id=source.organization_id,
        workspace_id=source.workspace_id,
        assistant_id=assistant.id,
        type="manual_faq",
        status="completed",
    )
    db.add(container)
    await db.flush()

    db.add(
        FaqEntry(
            knowledge_source_id=container.id,
            generated_from_knowledge_source_id=source.id,
            question="Already asked?",
            answer="Yes.",
        )
    )
    await db.flush()

    llm = _CountingLLM()

    await knowledge_source_service.generate_faqs_for_file_source(
        db,
        _FailingStorage(),
        llm,
        embedding_provider,
        knowledge_source_id=source.id,
    )

    assert llm.calls == 0


async def test_a_source_with_no_entries_is_retried(
    db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    """
    The recovery path: a source left empty by an exhausted allowance gets
    another go.
    """

    source, _assistant = await _make_source(db, "faq-retry-empty")

    class _Storage:
        async def download(self, key: str) -> bytes:
            return b"Some document text about the business and what it offers."

    llm = _CountingLLM()

    await knowledge_source_service.generate_faqs_for_file_source(
        db,
        _Storage(),
        llm,
        embedding_provider,
        knowledge_source_id=source.id,
    )

    assert llm.calls >= 1


async def test_an_unknown_source_is_a_no_op(
    db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    llm = _CountingLLM()

    await knowledge_source_service.generate_faqs_for_file_source(
        db,
        _FailingStorage(),
        llm,
        embedding_provider,
        knowledge_source_id=uuid.uuid4(),
    )

    assert llm.calls == 0
