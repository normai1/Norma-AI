"""
A knowledge source says "completed" when it is finished, not when its text
happens to be embedded.

Reported as: "PDF status showing completed even when it didn't generate no
faqs". Parsing, chunking and embedding finish inside the upload; writing the
FAQs is a background job that queues behind the model provider's token
budget and takes about fourteen minutes for 50 pages. Marking the source
completed at the end of the first step told the operator the work was done
when most of it had not started, and made a document that produced nothing
indistinguishable from one still working.
"""


from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.models.document import Document
from app.models.knowledge_source import KnowledgeSource
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.providers.mock_embedding import MockEmbeddingProvider
from app.services import knowledge_source as knowledge_source_service
from app.services.faq_generation import GenerationOutcome


def _outcome(**overrides) -> GenerationOutcome:
    base = {"windows": 4, "windows_covered": 4, "entries": 12}

    return GenerationOutcome(**{**base, **overrides})


def test_a_run_that_read_the_whole_document_completes_the_source() -> None:
    source = KnowledgeSource(type="file", status="processing")

    knowledge_source_service._apply_generation_outcome(source, _outcome())

    assert source.status == "completed"
    assert source.error_message is None


def test_a_run_stopped_partway_does_not_claim_to_be_finished() -> None:
    """
    The case that prompted this. Five windows of thirteen is a third of a
    document, and calling that completed is the same lie the old behaviour
    told - only later in the process.
    """

    source = KnowledgeSource(type="file", status="processing")

    knowledge_source_service._apply_generation_outcome(
        source,
        _outcome(
            windows=13,
            windows_covered=5,
            entries=21,
            stopped_reason="The AI provider's daily limit was reached.",
        ),
    )

    assert source.status == "failed"
    assert source.error_message == "The AI provider's daily limit was reached."


def test_the_reason_is_something_an_operator_can_act_on() -> None:
    """
    They cannot act on a 429 or a token budget. They can act on knowing the
    document was only partly read and that retrying will finish it.
    """

    from app.services.faq_generation import LLMQuotaExhausted, _stopped_reason

    daily = _stopped_reason(
        LLMQuotaExhausted(exhausted_window="day", retry_after_seconds=1169.0)
    )

    assert daily is not None
    assert "daily limit" in daily
    assert "Retry" in daily
    assert "429" not in daily
    assert "token" not in daily.lower()


def test_nothing_to_stop_for_means_no_reason() -> None:
    from app.services.faq_generation import _stopped_reason

    assert _stopped_reason(None) is None


async def _file_source(db: AsyncSession, slug: str) -> KnowledgeSource:
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
        status="failed",
        error_message="stopped partway",
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

    return source


class _Storage:
    async def download(self, key: str) -> bytes:
        return b"The clinic opens at nine and closes at five on weekdays."


class _WorkingLLM:
    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        return '[{"question": "When do you open?", "answer": "Nine."}]'


async def test_retrying_a_partial_run_finishes_the_source(
    db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    """
    A source left failed by an exhausted allowance has to be able to reach
    completed later. Keying "already done" off whether entries exist would
    have made exactly that case unrecoverable.
    """

    source = await _file_source(db, "status-retry")

    await knowledge_source_service.generate_faqs_for_file_source(
        db,
        _Storage(),
        _WorkingLLM(),
        embedding_provider,
        knowledge_source_id=source.id,
    )

    assert source.status == "completed"
    assert source.error_message is None


async def test_a_source_that_already_finished_is_not_run_again(
    db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    source = await _file_source(db, "status-done")
    source.status = "completed"
    await db.flush()

    class _Exploding:
        async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
            raise AssertionError("a finished source must not be generated again")

    await knowledge_source_service.generate_faqs_for_file_source(
        db,
        _Storage(),
        _Exploding(),
        embedding_provider,
        knowledge_source_id=source.id,
    )

    assert source.status == "completed"
