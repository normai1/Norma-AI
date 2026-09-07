"""
Internal, service-to-service route exposing the assistant configuration the
realtime LLM turn loop needs once per session: the resolved system prompt,
creativity, and the topics the assistant must not discuss (item 24c).
Resolved once at session setup, not per turn. Resolved once at session setup,
not per turn - unlike retrieval (app/api/internal/retrieval.py), an
assistant's prompt/persona/creativity do not change mid-call.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.internal_deps import RequireInternalSecret
from app.core.exceptions import AssistantNotFound
from app.services.llm_config import resolve_llm_config

router = APIRouter(tags=["internal"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)


@router.get("/internal/v1/assistants/{assistant_id}/llm-config")
async def get_llm_config(
    assistant_id: uuid.UUID,
    db: DbSession,
    _: RequireInternalSecret,
) -> dict[str, str | float | list[str]]:
    try:
        config = await resolve_llm_config(db, assistant_id)
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return {
        "system_prompt": config.system_prompt,
        "creativity": config.creativity,
        "blocked_topics": config.blocked_topics,
    }
