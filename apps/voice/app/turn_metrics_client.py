"""
Fire-and-forget POST of one completed turn's metrics to apps/api's internal
API (item 20f). Unlike every other apps/voice client, this is never awaited
inline in the critical path - TTSProcessor fires it via asyncio.create_task
right after finish_turn() - and it never raises: a lost metric must never
affect the call, and there is nothing useful to "fail open" to here, unlike
fetch_retrieved_context's empty-string fallback.
"""

import uuid

import httpx

from app import config
from app.turn_metrics import TurnMetricRecord


async def record_turn_metric(
    assistant_id: uuid.UUID,
    record: TurnMetricRecord,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    owned_client = client or httpx.AsyncClient()

    try:
        await owned_client.post(
            f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/turn-metrics",
            json={
                "call_id": str(record.call_id),
                "stt_finalized_at": _isoformat(record.stt_finalized_at),
                "retrieval_done_at": _isoformat(record.retrieval_done_at),
                "llm_first_token_at": _isoformat(record.llm_first_token_at),
                "llm_complete_at": _isoformat(record.llm_complete_at),
                "tts_first_byte_at": _isoformat(record.tts_first_byte_at),
                "audio_out_at": _isoformat(record.audio_out_at),
            },
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            timeout=5.0,
        )
    except httpx.HTTPError:
        pass
    finally:
        if client is None:
            await owned_client.aclose()


def _isoformat(value: object) -> str | None:
    return value.isoformat() if value is not None else None
