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
from pydantic import BaseModel, Field

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
    """
    Everything apps/voice knows about one finished turn.

    Item 25b's four fields all default to None so an older voice worker,
    which sends none of them, still records the latency row it always did -
    the two planes deploy separately and briefly run different code
    (CLAUDE.md section 6.2).

    The token counts are bounded rather than accepted as any integer. They
    come from another service over an authenticated channel, so this is not
    a trust boundary in the security sense, but a negative token count or an
    absurd cost is a bug somewhere upstream, and the useful place to notice
    it is the moment it tries to enter the billing data rather than in a
    margin report months later.
    """

    call_id: uuid.UUID
    turn_id: uuid.UUID | None = None
    stt_finalized_at: datetime | None = None
    retrieval_done_at: datetime | None = None
    llm_first_token_at: datetime | None = None
    llm_complete_at: datetime | None = None
    tts_first_byte_at: datetime | None = None
    audio_out_at: datetime | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    cost_micro_usd: int | None = Field(default=None, ge=0)


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
            turn_id=body.turn_id,
            stt_finalized_at=body.stt_finalized_at,
            retrieval_done_at=body.retrieval_done_at,
            llm_first_token_at=body.llm_first_token_at,
            llm_complete_at=body.llm_complete_at,
            tts_first_byte_at=body.tts_first_byte_at,
            audio_out_at=body.audio_out_at,
            prompt_tokens=body.prompt_tokens,
            completion_tokens=body.completion_tokens,
            cost_micro_usd=body.cost_micro_usd,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    # Without this the row is flushed - which is enough to return an id, so
    # the caller sees 200 - and then rolled back when the request ends. Every
    # turn metric ever recorded was discarded that way, leaving the table
    # empty and per-turn latency (CLAUDE.md section 27) unmeasurable.
    await db.commit()

    return {"id": str(turn_metric.id)}
