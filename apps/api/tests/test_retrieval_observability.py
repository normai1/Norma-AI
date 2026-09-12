"""
Build-plan item 50a. Every turn's retrieval is inspectable.

Pulled forward ahead of telephony because a crawled site produced answers
that mixed unrelated chunks, invented details, and contradicted the source -
and none of that is diagnosable while retrieval is a black box returning a
string. The open question is whether wrong answers come from wrong
retrieval or from the model misusing correct retrieval, and today there is
no way to tell those apart.
"""

import logging
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.assistant import Assistant
from app.models.chunk import Chunk
from app.models.knowledge_source import KnowledgeSource
from app.models.organization import Organization
from app.models.workspace import Workspace
from app.providers.mock_embedding import MockEmbeddingProvider
from app.services.context_builder import build_context, chunks_that_fit
from app.services.retrieval import RetrievedChunk

_RETRIEVE_URL = "/internal/v1/assistants/{assistant_id}/retrieve"


def _chunk(text: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        knowledge_source_id=uuid.uuid4(),
        source_type="website",
        text=text,
        metadata={},
        score=score,
    )


def test_what_reaches_the_model_is_reported_by_the_same_rule_that_packs_it() -> None:
    """
    A chunk retrieved and then dropped for space looks, from outside,
    exactly like one never retrieved - and when an answer is wrong those are
    completely different problems.
    """

    chunks = [_chunk("a" * 300), _chunk("b" * 300), _chunk("c" * 300)]

    kept = chunks_that_fit(chunks, max_chars=650)
    context = build_context(chunks, max_chars=650)

    assert len(kept) == 2
    # The report and the prompt cannot disagree: same function.
    assert context == "a" * 300 + "\n\n" + "b" * 300


def test_nothing_retrieved_is_reported_as_nothing() -> None:
    assert chunks_that_fit([]) == []
    assert build_context([]) == ""


async def _assistant_with_one_chunk(
    db: AsyncSession, embedding_provider: MockEmbeddingProvider, slug: str, text: str
) -> Assistant:
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
        type="website",
        status="completed",
    )
    db.add(source)
    await db.flush()

    [vector] = await embedding_provider.embed([text])
    db.add(
        Chunk(
            organization_id=organization.id,
            workspace_id=workspace.id,
            assistant_id=assistant.id,
            knowledge_source_id=source.id,
            text=text,
            ordering=0,
            chunk_metadata={},
            embedding=vector,
        )
    )
    await db.flush()

    return assistant


async def test_a_turn_reports_which_chunks_it_was_given_and_how_well_they_scored(
    client: AsyncClient, db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    assistant = await _assistant_with_one_chunk(
        db, embedding_provider, "obs-scores", "We are open nine to five."
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": "We are open nine to five."},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    body = response.json()
    [item] = body["retrieved"]

    assert item["source_type"] == "website"
    assert isinstance(item["score"], float)
    assert item["used"] is True
    assert item["chars"] == len("We are open nine to five.")
    # Identifiers, so an answer can be traced to the document behind it.
    assert uuid.UUID(item["knowledge_source_id"])
    assert uuid.UUID(item["chunk_id"])


async def test_the_report_never_carries_the_knowledge_itself(
    client: AsyncClient, db: AsyncSession, embedding_provider: MockEmbeddingProvider
) -> None:
    """
    CLAUDE.md section 27: chunk text is document content and does not belong
    in a diagnostic payload any more than it belongs in a log line.
    """

    secret = "The cancellation policy is fourteen days notice."
    assistant = await _assistant_with_one_chunk(
        db, embedding_provider, "obs-no-text", secret
    )

    response = await client.post(
        _RETRIEVE_URL.format(assistant_id=assistant.id),
        json={"query": secret},
        headers={"X-Internal-Secret": settings.internal_api_secret},
    )

    reported = str(response.json()["retrieved"])

    assert "cancellation" not in reported
    assert "fourteen" not in reported


async def test_the_decision_is_logged_without_the_words(
    client: AsyncClient,
    db: AsyncSession,
    embedding_provider: MockEmbeddingProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = "What is the Quaxton cancellation policy?"
    assistant = await _assistant_with_one_chunk(
        db, embedding_provider, "obs-log", "Wilhelmina handles cancellations."
    )

    with caplog.at_level(logging.INFO):
        await client.post(
            _RETRIEVE_URL.format(assistant_id=assistant.id),
            json={"query": question},
            headers={"X-Internal-Secret": settings.internal_api_secret},
        )

    logged = "\n".join(
        record.getMessage()
        for record in caplog.records
        if "retrieval:" in record.getMessage()
    )

    assert "scores=" in logged
    assert str(assistant.id) in logged
    # Neither the caller's question nor the knowledge.
    assert "Quaxton" not in logged
    assert "Wilhelmina" not in logged
