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

from app.core.exceptions import AssistantNotFound
from app.providers.embedding import EmbeddingProvider
from app.repositories import assistant as assistant_repo
from app.repositories import chunk as chunk_repo

DEFAULT_TOP_K = 5


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
) -> list[RetrievedChunk]:
    """
    Embed query and return the top_k most similar chunks in this
    organization/workspace/assistant, most similar first. assistant_id is
    validated (must belong to workspace_id) and narrows the search to only
    that assistant's own knowledge sources (feature 23d).
    """

    await _assert_assistant_in_workspace(
        db, workspace_id=workspace_id, assistant_id=assistant_id
    )

    [query_vector] = await embedding_provider.embed([query])

    rows = await chunk_repo.search_by_similarity(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        query_vector=query_vector,
        top_k=top_k,
    )

    return [
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
