"""
Internal, service-to-service route persisting one turn's TurnMetric row
(item 20f) - called once per turn, when apps/voice's TTSProcessor finishes
accumulating that turn's per-leg timestamps. Resolves assistant_id to its
organization_id/workspace_id first, exactly like the existing internal
retrieval and tts-config endpoints already do.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import DbSession
from app.api.internal_deps import RequireInternalSecret
from app.core.exceptions import AssistantNotFound
from app.services.turn_metrics import record_turn_metric

router = APIRouter(tags=["internal"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)


class TurnMetricRequest(BaseModel):
    call_id: uuid.UUID
    stt_finalized_at: datetime | None = None
    retrieval_done_at: datetime | None = None
    llm_first_token_at: datetime | None = None
    llm_complete_at: datetime | None = None
    tts_first_byte_at: datetime | None = None
    audio_out_at: datetime | None = None


@router.post("/internal/v1/assistants/{assistant_id}/turn-metrics")
async def create_turn_metric(
    assistant_id: uuid.UUID,
    body: TurnMetricRequest,
    db: DbSession,
    _: RequireInternalSecret,
) -> dict[str, str]:
    try:
        turn_metric = await record_turn_metric(
            db,
            assistant_id=assistant_id,
            call_id=body.call_id,
            stt_finalized_at=body.stt_finalized_at,
            retrieval_done_at=body.retrieval_done_at,
            llm_first_token_at=body.llm_first_token_at,
            llm_complete_at=body.llm_complete_at,
            tts_first_byte_at=body.tts_first_byte_at,
            audio_out_at=body.audio_out_at,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return {"id": str(turn_metric.id)}
