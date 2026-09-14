"""
Semantic retrieval: embeds a query, runs a tenant-scoped pgvector
similarity search, and returns each match with its source attribution. A
read-only service function - item 20's per-turn context assembly stage is
this function's first live caller, once it exists. No route yet: see
feature 19's spec for why.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AssistantNotFound
from app.providers.embedding import EmbeddingProvider
from app.repositories import assistant as assistant_repo
from app.repositories import chunk as chunk_repo
from app.services.query_embedding_cache import embed_query, is_query_cached
from app.services.retrieval_tracing import trace_step

logger = logging.getLogger(__name__)

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
    #
    # Traced separately from the search below because the two are orders of
    # magnitude apart - a hosted embedding call against a single-digit
    # millisecond index scan - and "which one was slow" is the first thing
    # anyone asks about a turn that blew its budget. The span records
    # whether the cache had it, since that is the difference between the two
    # timings meaning anything at all.
    with trace_step(
        "embed_query",
        # "embedding", not "embedder". LangSmith validates run_type against a
        # fixed set server-side and rejects the *whole batch* a bad one
        # arrives in, so a wrong name here silently loses the sibling runs
        # too - which is how this was found: the root retrieval run vanished
        # and only an unrelated child survived.
        "embedding",
        model=settings.embedding_model,
        provider=settings.embedding_provider,
        cache_hit=is_query_cached(settings.embedding_model, query),
    ):
        query_vector = await embed_query(
            embedding_provider, settings.embedding_model, query
        )

    with trace_step(
        "vector_search",
        "retriever",
        top_k=top_k,
        dimension=len(query_vector),
    ):
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


# What is asked at session start purely to warm the path, when there is no
# real question yet. The content is irrelevant - only the work matters.
_WAKE_QUERY = "hello"


async def wake_retrieval_path(
    db: AsyncSession,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> None:
    """
    Run one complete retrieval and throw the result away, so the first turn
    of a call does not pay for a cold path.

    Both legs are cold in their own way and the expensive one is not the
    obvious one. The hosted embedding provider is slow and erratic after an
    idle period, which is well known here. The vector search is worse:
    measured straight after the knowledge base was re-indexed, the first
    search took 4.14s and the next five took 55-118ms, because the query
    reads on the order of 35,000 buffer pages and the first one reads them
    from disk rather than from PostgreSQL's cache.

    Against the media plane's 1.5s per-turn retrieval budget that first turn
    is abandoned - and an abandoned turn warms nothing, so the next one pays
    the same cost, and the next. Measured over eight consecutive turns after
    a restart, half of them ran out of budget and answered with no knowledge
    at all. Warming only the embedding provider does not help and cannot:
    three patient provider wake-ups in a row still left the following three
    turns timing out, because none of them touched the search.

    Called once per session, before the caller has said anything, while the
    greeting is playing - the one moment when several seconds cost nobody
    anything.

    Failure is not raised. The endpoint above is best-effort by contract,
    and a warm that did not work leaves the call exactly where it would have
    been without it.
    """

    try:
        await retrieve(
            db,
            embedding_provider,
            organization_id=organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            query=_WAKE_QUERY,
        )
    except Exception:
        logger.info("retrieval path warm-up did not complete", exc_info=True)
