import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from norma_shared.correlation import (
    CALL_ID_HEADER,
    TURN_ID_HEADER,
    CallContext,
    bind_call_context,
    unbind_call_context,
)

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


async def test_the_post_is_filed_under_the_turn_it_describes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 25b. This POST is fired *after* finish_turn() has already advanced
    to the next turn, so the ambient correlation context no longer names the
    turn being reported. Stamping the request from the record rather than
    from the context is what keeps the control plane's log line about it
    filed under the right turn.
    """

    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    reported = TurnMetricRecord(call_id=uuid.uuid4())
    # The session has already moved on, exactly as it has in production by
    # the time this request goes out.
    token = bind_call_context(
        CallContext(call_id=reported.call_id, turn_id=uuid.uuid4())
    )

    sent_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_headers.update(request.headers)

        return httpx.Response(200, json={"id": str(uuid.uuid4())})

    try:
        await record_turn_metric(
            _ASSISTANT_ID, reported, client=_client_returning(handler)
        )
    finally:
        unbind_call_context(token)

    assert sent_headers[CALL_ID_HEADER.lower()] == str(reported.call_id)
    assert sent_headers[TURN_ID_HEADER.lower()] == str(reported.turn_id)


async def test_the_record_carries_its_tokens_and_cost_to_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    record = TurnMetricRecord(
        call_id=uuid.uuid4(),
        prompt_tokens=412,
        completion_tokens=37,
        cost_micro_usd=90,
    )
    posted = {}

    def handler(request: httpx.Request) -> httpx.Response:
        posted.update(json.loads(request.content))

        return httpx.Response(200, json={"id": str(uuid.uuid4())})

    await record_turn_metric(_ASSISTANT_ID, record, client=_client_returning(handler))

    assert posted["turn_id"] == str(record.turn_id)
    assert posted["prompt_tokens"] == 412
    assert posted["completion_tokens"] == 37
    assert posted["cost_micro_usd"] == 90
