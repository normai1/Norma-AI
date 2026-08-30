import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.turn_metric import TurnMetric


async def create(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    call_id: uuid.UUID,
    stt_finalized_at: datetime | None = None,
    retrieval_done_at: datetime | None = None,
    llm_first_token_at: datetime | None = None,
    llm_complete_at: datetime | None = None,
    tts_first_byte_at: datetime | None = None,
    audio_out_at: datetime | None = None,
) -> TurnMetric:
    """
    Insert one turn's metrics row. Every leg is optional - a turn that
    failed or was interrupted partway simply has the legs it never reached
    left null.
    """

    turn_metric = TurnMetric(
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        call_id=call_id,
        stt_finalized_at=stt_finalized_at,
        retrieval_done_at=retrieval_done_at,
        llm_first_token_at=llm_first_token_at,
        llm_complete_at=llm_complete_at,
        tts_first_byte_at=tts_first_byte_at,
        audio_out_at=audio_out_at,
    )

    db.add(turn_metric)
    await db.flush()

    return turn_metric


async def list_since(db: AsyncSession, since: datetime) -> list[TurnMetric]:
    """
    Every turn metric row created at or after `since` - what a p95
    computation reads from.
    """

    result = await db.scalars(
        select(TurnMetric)
        .where(TurnMetric.created_at >= since)
        .order_by(TurnMetric.created_at),
    )

    return list(result.all())
