import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app import config
from app.turn_metrics import TurnMetricRecord
from app.turn_metrics_client import record_turn_metric

_ASSISTANT_ID = uuid.uuid4()


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_posts_the_full_record_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    call_id = uuid.uuid4()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = TurnMetricRecord(
        call_id=call_id,
        stt_finalized_at=now,
        audio_out_at=now,
    )

    posted = {}

    def handler(request: httpx.Request) -> httpx.Response:
        posted.update(json.loads(request.content))
        assert request.headers["X-Internal-Secret"] == "the-real-secret"
        assert str(_ASSISTANT_ID) in str(request.url)
        assert "turn-metrics" in str(request.url)

        return httpx.Response(200, json={"id": str(uuid.uuid4())})

    await record_turn_metric(_ASSISTANT_ID, record, client=_client_returning(handler))

    assert posted["call_id"] == str(call_id)
    assert posted["stt_finalized_at"] == now.isoformat()
    assert posted["audio_out_at"] == now.isoformat()
    assert posted["retrieval_done_at"] is None
    assert posted["llm_first_token_at"] is None
    assert posted["llm_complete_at"] is None
    assert posted["tts_first_byte_at"] is None


async def test_silently_swallows_a_non_200_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    record = TurnMetricRecord(call_id=uuid.uuid4())

    # Must not raise.
    await record_turn_metric(_ASSISTANT_ID, record, client=_client_returning(handler))


async def test_silently_swallows_a_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    record = TurnMetricRecord(call_id=uuid.uuid4())

    # Must not raise.
    await record_turn_metric(_ASSISTANT_ID, record, client=_client_returning(handler))
