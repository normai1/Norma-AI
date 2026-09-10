"""
The per-turn query embedding must not pay for a new TLS handshake.

Both hosted embedding providers were written to open an httpx.AsyncClient per
embed() call and close it again. That is harmless on the ingestion paths, but
retrieval embeds the caller's question inside a live turn. Measured against
router.huggingface.co with BAAI/bge-base-en-v1.5, one text:

    client created per call:   1.598s, 1.022s, 0.882s, 0.998s
    one kept-alive client:     0.333s, 0.306s, 0.302s, 0.284s, 0.283s
"""

import httpx
import pytest

from app.core.config import settings
from app.providers.embedding_http_client import (
    close_embedding_http_client,
    get_embedding_http_client,
)
from app.providers.factory import get_embedding_provider


@pytest.fixture(autouse=True)
async def _close_shared_client():
    yield
    await close_embedding_http_client()


async def test_the_shared_client_is_reused_across_calls() -> None:
    assert get_embedding_http_client() is get_embedding_http_client()


async def test_the_shared_client_keeps_connections_alive() -> None:
    """
    A pool that closed idle connections between turns would hand back the
    handshake this exists to avoid.
    """

    pool = get_embedding_http_client()._transport._pool

    assert pool._max_keepalive_connections > 0
    # Long enough to survive the gaps between one caller's turns.
    assert pool._keepalive_expiry >= 60.0


async def test_closing_is_idempotent() -> None:
    get_embedding_http_client()

    await close_embedding_http_client()
    await close_embedding_http_client()


async def test_a_closed_client_is_replaced_rather_than_reused() -> None:
    first = get_embedding_http_client()
    await close_embedding_http_client()
    second = get_embedding_http_client()

    assert second is not first
    assert not second.is_closed


async def test_the_huggingface_provider_is_built_with_the_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "hf_token", "a-token")

    provider = get_embedding_provider("huggingface")

    assert provider._client is get_embedding_http_client()


async def test_the_openai_provider_is_built_with_the_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "a-key")

    provider = get_embedding_provider("openai")

    assert provider._client is get_embedding_http_client()


async def test_an_injected_client_is_not_closed_by_the_provider() -> None:
    """
    The providers close only a client they created themselves. If that
    branch ever flipped, the first embed() would close the shared pool and
    every later turn would be back to a fresh handshake - or an error.
    """

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[[0.1, 0.2, 0.3]])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    from app.providers.huggingface_embedding import HuggingFaceEmbeddingProvider

    provider = HuggingFaceEmbeddingProvider(
        api_key="a-token", model="a-model", dimension=3, client=client
    )

    await provider.embed(["first question"])
    await provider.embed(["second question"])

    assert not client.is_closed
    assert len(requests) == 2

    await client.aclose()
