"""
Resolves an assistant_id to its organization/workspace and persists one
turn's TurnMetric row (item 20f), mirroring app/services/tts_config.py's
and app/api/internal/retrieval.py's exact assistant-resolution shape. Also
holds compute_time_to_first_audio_p95, the pure percentile computation
CLAUDE.md section 27's "p95 latency CI gate" reads from - kept here rather
than in the repository, since it is business logic (which two legs answer
"time to first audio") over rows the repository merely fetches.
"""

import uuid
from datetime import datetime

from norma_shared.latency import percentile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.models.turn_metric import TurnMetric
from app.repositories import assistant as assistant_repo
from app.repositories import turn_metric as turn_metric_repo


async def record_turn_metric(
    db: AsyncSession,
    *,
    assistant_id: uuid.UUID,
    call_id: uuid.UUID,
    stt_finalized_at: datetime | None = None,
    retrieval_done_at: datetime | None = None,
    llm_first_token_at: datetime | None = None,
    llm_complete_at: datetime | None = None,
    tts_first_byte_at: datetime | None = None,
    audio_out_at: datetime | None = None,
) -> TurnMetric:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise AssistantNotFound

    return await turn_metric_repo.create(
        db,
        organization_id=assistant.organization_id,
        workspace_id=assistant.workspace_id,
        assistant_id=assistant_id,
        call_id=call_id,
        stt_finalized_at=stt_finalized_at,
        retrieval_done_at=retrieval_done_at,
        llm_first_token_at=llm_first_token_at,
        llm_complete_at=llm_complete_at,
        tts_first_byte_at=tts_first_byte_at,
        audio_out_at=audio_out_at,
    )


def compute_time_to_first_audio_p95(rows: list[TurnMetric]) -> float | None:
    """
    p95 time-to-first-audio in milliseconds, over rows where both
    stt_finalized_at and audio_out_at are present - the same "caller stops
    speaking" -> "first audio out" span CLAUDE.md section 1's non-negotiable
    budget is stated against. None if no row qualifies.
    """

    durations_ms = [
        (row.audio_out_at - row.stt_finalized_at).total_seconds() * 1000
        for row in rows
        if row.stt_finalized_at is not None and row.audio_out_at is not None
    ]

    return percentile(durations_ms, 0.95)
