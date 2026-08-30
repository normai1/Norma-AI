"""
Internal, service-to-service route exposing the two pieces of assistant
configuration the realtime LLM turn loop (item 20d) needs once per session:
the resolved system prompt and creativity. Resolved once at session setup,
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
) -> dict[str, str | float]:
    try:
        config = await resolve_llm_config(db, assistant_id)
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return {"system_prompt": config.system_prompt, "creativity": config.creativity}
