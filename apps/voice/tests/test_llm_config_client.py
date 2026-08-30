import uuid

import httpx
import pytest

from app import config
from app.llm_config_client import (
    DEFAULT_CREATIVITY,
    DEFAULT_SYSTEM_PROMPT,
    fetch_llm_config,
)

_ASSISTANT_ID = uuid.uuid4()


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_returns_the_config_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Secret"] == "the-real-secret"
        assert str(_ASSISTANT_ID) in str(request.url)

        return httpx.Response(200, json={"system_prompt": "Be warm.", "creativity": 0.8})

    result = await fetch_llm_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.system_prompt == "Be warm."
    assert result.creativity == 0.8


async def test_returns_the_default_on_non_200_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    result = await fetch_llm_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert result.creativity == DEFAULT_CREATIVITY


async def test_returns_the_default_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await fetch_llm_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert result.creativity == DEFAULT_CREATIVITY


async def test_returns_the_default_for_a_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"system_prompt": None, "creativity": "not-a-number"})

    result = await fetch_llm_config(_ASSISTANT_ID, client=_client_returning(handler))

    assert result.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert result.creativity == DEFAULT_CREATIVITY
