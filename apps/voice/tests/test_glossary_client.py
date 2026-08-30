import uuid

import httpx
import pytest

from app import config
from app.glossary_client import fetch_glossary_terms

_ASSISTANT_ID = uuid.uuid4()


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_returns_terms_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "the-real-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Secret"] == "the-real-secret"
        assert str(_ASSISTANT_ID) in str(request.url)

        return httpx.Response(200, json={"terms": ["tinnitus", "otoscopy"]})

    terms = await fetch_glossary_terms(_ASSISTANT_ID, client=_client_returning(handler))

    assert terms == ["tinnitus", "otoscopy"]


async def test_returns_empty_list_on_non_200_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    terms = await fetch_glossary_terms(_ASSISTANT_ID, client=_client_returning(handler))

    assert terms == []


async def test_returns_empty_list_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    terms = await fetch_glossary_terms(_ASSISTANT_ID, client=_client_returning(handler))

    assert terms == []


async def test_returns_empty_list_for_a_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"terms": "not-a-list"})

    terms = await fetch_glossary_terms(_ASSISTANT_ID, client=_client_returning(handler))

    assert terms == []
