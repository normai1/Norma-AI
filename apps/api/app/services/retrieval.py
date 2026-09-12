"""
Semantic retrieval: embeds a query, runs a tenant-scoped pgvector
similarity search, and returns each match with its source attribution. A
read-only service function - item 20's per-turn context assembly stage is
this function's first live caller, once it exists. No route yet: see
feature 19's spec for why.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AssistantNotFound
from app.providers.embedding import EmbeddingProvider
from app.repositories import assistant as assistant_repo
from app.repositories import chunk as chunk_repo
from app.services.query_embedding_cache import embed_query

DEFAULT_TOP_K = 5

# A floor that cannot exclude anything, for callers that want the raw ranking.
#
# Not 0.0: score is 1 - cosine distance, and cosine distance runs 0 to 2, so a
# chunk pointing away from the query scores below zero. A floor of 0.0 quietly
# drops those, which is a filter rather than the absence of one.
NO_MIN_SCORE = -1.0


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    knowledge_source_id: uuid.UUID
    source_type: str
    text: str
    metadata: dict[str, Any]
    score: float


async def _assert_assistant_in_workspace(
    db: AsyncSession, *, workspace_id: uuid.UUID, assistant_id: uuid.UUID
) -> None:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None or assistant.workspace_id != workspace_id:
        raise AssistantNotFound


async def retrieve(
    db: AsyncSession,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """
    Embed query and return the most similar chunks in this
    organization/workspace/assistant, most similar first - at most top_k of
    them, and only those scoring at least min_score (defaulting to
    settings.retrieval_min_score).

    Returning fewer than top_k, or none at all, is a normal outcome: it is
    what "the knowledge does not cover this" looks like, and it is the
    difference between the assistant saying so and inventing an answer from
    whatever happened to be nearest. assistant_id is
    validated (must belong to workspace_id) and narrows the search to only
    that assistant's own knowledge sources (feature 23d).
    """

    await _assert_assistant_in_workspace(
        db, workspace_id=workspace_id, assistant_id=assistant_id
    )

    if min_score is None:
        min_score = settings.retrieval_min_score

    # Cached, because this embed call is the slowest thing in the turn's
    # retrieval and callers repeat each other constantly. See
    # services/query_embedding_cache.py.
    query_vector = await embed_query(
        embedding_provider, settings.embedding_model, query
    )

    rows = await chunk_repo.search_by_similarity(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        query_vector=query_vector,
        top_k=top_k,
    )

    retrieved = [
        RetrievedChunk(
            chunk_id=chunk.id,
            knowledge_source_id=chunk.knowledge_source_id,
            source_type=source_type,
            text=chunk.text,
            metadata=chunk.chunk_metadata,
            score=1.0 - distance,
        )
        for chunk, source_type, distance in rows
    ]

    # Anything too far away is dropped rather than handed over as the
    # least-bad match. A question the knowledge does not answer should
    # produce no context, so the model answers from the standing guardrail
    # rule - "I do not have that detail" - instead of from five chunks that
    # happen to be nearest in a corpus that never mentioned the subject.
    #
    # Applied here rather than in the context builder so every caller gets
    # it, and so the observability that reports scores reports the same set
    # the model was given.
    return [chunk for chunk in retrieved if chunk.score >= min_score]
