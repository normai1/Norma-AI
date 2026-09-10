"""
Internal, service-to-service route composing item 19's retrieve() and
build_context() into one finished string - called once per turn (the query
changes every time), unlike app/api/internal/llm_config.py's once-per-session
config. Adds no new retrieval logic; just resolves assistant_id to its
organization_id/workspace_id first, exactly like the existing internal
glossary and turn-detection-config endpoints already do.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, EmbeddingProviderDep
from app.api.internal_deps import RequireInternalSecret
from app.core.config import settings
from app.repositories import assistant as assistant_repo
from app.repositories import faq_entry as faq_entry_repo
from app.services.context_builder import build_context
from app.services.query_embedding_cache import warm_query_embeddings
from app.services.retrieval import retrieve

router = APIRouter(tags=["internal"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1)


@router.post("/internal/v1/assistants/{assistant_id}/retrieve")
async def retrieve_context(
    assistant_id: uuid.UUID,
    body: RetrieveRequest,
    db: DbSession,
    embedding_provider: EmbeddingProviderDep,
    _: RequireInternalSecret,
) -> dict[str, str]:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise _ASSISTANT_NOT_FOUND

    chunks = await retrieve(
        db,
        embedding_provider,
        organization_id=assistant.organization_id,
        workspace_id=assistant.workspace_id,
        assistant_id=assistant_id,
        query=body.query,
    )

    return {"context": build_context(chunks)}


@router.post("/internal/v1/assistants/{assistant_id}/retrieve/warm")
async def warm_retrieval_cache(
    assistant_id: uuid.UUID,
    db: DbSession,
    embedding_provider: EmbeddingProviderDep,
    _: RequireInternalSecret,
) -> dict[str, int]:
    """
    Embed this assistant's FAQ questions into the query embedding cache, so
    the phrasings callers actually use are already there when the first turn
    arrives.

    Called once per session, before the conversation starts, rather than
    from inside a turn: the whole point is to spend the hosted provider's
    unpredictable seconds while the greeting is playing instead of while the
    caller is waiting for an answer. One batched provider call, so the cost
    is one request regardless of how many questions the assistant has.

    Best-effort by design. The caller treats any failure as "not warmed" and
    the turn path is unchanged - it just falls back to embedding each
    question the first time it is asked, which is exactly what happened
    before this existed.
    """

    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise _ASSISTANT_NOT_FOUND

    questions = await faq_entry_repo.list_questions_for_assistant(db, assistant_id)

    warmed = await warm_query_embeddings(
        embedding_provider, settings.embedding_model, questions
    )

    return {"warmed": warmed}
