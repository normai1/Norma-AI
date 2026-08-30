import uuid

import httpx
import pytest

from app import config
from app.turn_detection_client import DEFAULT_SENSITIVITY, fetch_turn_sensitivity

_ASSISTANT_ID = uuid.uuid4()


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_returns_sensitivity_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Secret"] == "the-real-secret"
        assert str(_ASSISTANT_ID) in str(request.url)

        return httpx.Response(200, json={"sensitivity": 0.8})

    sensitivity = await fetch_turn_sensitivity(
        _ASSISTANT_ID, client=_client_returning(handler)
    )

    assert sensitivity == 0.8


async def test_returns_the_default_on_non_200_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    sensitivity = await fetch_turn_sensitivity(
        _ASSISTANT_ID, client=_client_returning(handler)
    )

    assert sensitivity == DEFAULT_SENSITIVITY


async def test_returns_the_default_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    sensitivity = await fetch_turn_sensitivity(
        _ASSISTANT_ID, client=_client_returning(handler)
    )

    assert sensitivity == DEFAULT_SENSITIVITY


async def test_returns_the_default_for_a_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sensitivity": "not-a-number"})

    sensitivity = await fetch_turn_sensitivity(
        _ASSISTANT_ID, client=_client_returning(handler)
    )

    assert sensitivity == DEFAULT_SENSITIVITY
