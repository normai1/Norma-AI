import uuid

import httpx
import pytest

from app import config
from app.tts_config_client import (
    DEFAULT_SPEECH_RATE,
    DEFAULT_VOICE_ID,
    fetch_tts_config,
)

_ASSISTANT_ID = uuid.uuid4()


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_returns_the_config_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Secret"] == "the-real-secret"
        assert str(_ASSISTANT_ID) in str(request.url)

        return httpx.Response(200, json={"voice_id": "voice-7", "speech_rate": 0.8})

    result = await fetch_tts_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.voice_id == "voice-7"
    assert result.speech_rate == 0.8


async def test_returns_the_default_on_non_200_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    result = await fetch_tts_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.voice_id == DEFAULT_VOICE_ID
    assert result.speech_rate == DEFAULT_SPEECH_RATE


async def test_returns_the_default_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await fetch_tts_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.voice_id == DEFAULT_VOICE_ID
    assert result.speech_rate == DEFAULT_SPEECH_RATE


async def test_returns_the_default_for_a_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"voice_id": None, "speech_rate": "not-a-number"})

    result = await fetch_tts_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.voice_id == DEFAULT_VOICE_ID
    assert result.speech_rate == DEFAULT_SPEECH_RATE
