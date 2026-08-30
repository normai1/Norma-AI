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
from app.repositories import assistant as assistant_repo
from app.services.context_builder import build_context
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
