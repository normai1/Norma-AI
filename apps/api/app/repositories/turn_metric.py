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
    turn_id: uuid.UUID | None = None,
    stt_finalized_at: datetime | None = None,
    retrieval_done_at: datetime | None = None,
    llm_first_token_at: datetime | None = None,
    llm_complete_at: datetime | None = None,
    tts_first_byte_at: datetime | None = None,
    audio_out_at: datetime | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_micro_usd: int | None = None,
) -> TurnMetric:
    """
    Insert one turn's metrics row. Every leg is optional - a turn that
    failed or was interrupted partway simply has the legs it never reached
    left null.

    So are item 25b's turn_id, tokens and cost, and for a second reason
    beyond partial turns: a voice worker running code older than this column
    sends none of them, which is a normal state during a staged deploy
    (CLAUDE.md section 6.2). A null cost means unknown, never free - see the
    model.
    """

    turn_metric = TurnMetric(
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        call_id=call_id,
        turn_id=turn_id,
        stt_finalized_at=stt_finalized_at,
        retrieval_done_at=retrieval_done_at,
        llm_first_token_at=llm_first_token_at,
        llm_complete_at=llm_complete_at,
        tts_first_byte_at=tts_first_byte_at,
        audio_out_at=audio_out_at,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_micro_usd=cost_micro_usd,
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
