"""
Internal, service-to-service route exposing the assistant configuration the
TTS stage (item 20e) needs once per session: voice_id and speech_rate.
Mirrors app/api/internal/llm_config.py's exact shape.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.internal_deps import RequireInternalSecret
from app.core.exceptions import AssistantNotFound
from app.services.tts_config import resolve_tts_config

router = APIRouter(tags=["internal"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)


@router.get("/internal/v1/assistants/{assistant_id}/tts-config")
async def get_tts_config(
    assistant_id: uuid.UUID,
    db: DbSession,
    _: RequireInternalSecret,
) -> dict[str, str | float]:
    try:
        config = await resolve_tts_config(db, assistant_id)
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return {"voice_id": config.voice_id, "speech_rate": config.speech_rate}
