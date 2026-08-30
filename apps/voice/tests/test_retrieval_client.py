import uuid

import httpx
import pytest

from app import config
from app.retrieval_client import fetch_retrieved_context

_ASSISTANT_ID = uuid.uuid4()


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_returns_the_context_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Secret"] == "the-real-secret"
        assert str(_ASSISTANT_ID) in str(request.url)
        assert request.content == b'{"query":"What are your hours?"}'

        return httpx.Response(200, json={"context": "We close at 5pm."})

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "What are your hours?", client=_client_returning(handler)
    )

    assert context == "We close at 5pm."


async def test_returns_empty_string_on_non_200_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "anything", client=_client_returning(handler)
    )

    assert context == ""


async def test_returns_empty_string_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "anything", client=_client_returning(handler)
    )

    assert context == ""


async def test_returns_empty_string_for_a_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"context": None})

    context = await fetch_retrieved_context(
        _ASSISTANT_ID, "anything", client=_client_returning(handler)
    )

    assert context == ""
