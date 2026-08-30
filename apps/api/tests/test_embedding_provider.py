import httpx
import pytest

from app.providers.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingProviderTimeout,
    EmbeddingProviderUnavailable,
)
from app.providers.mock_embedding import MockEmbeddingProvider
from app.providers.openai_embedding import OpenAIEmbeddingProvider


async def test_mock_embed_returns_a_vector_per_text() -> None:
    provider = MockEmbeddingProvider(dimension=8)

    vectors = await provider.embed(["hello", "world"])

    assert len(vectors) == 2
    assert all(len(vector) == 8 for vector in vectors)


async def test_mock_embed_is_deterministic() -> None:
    provider = MockEmbeddingProvider(dimension=8)

    first = await provider.embed(["hello"])
    second = await provider.embed(["hello"])

    assert first == second


async def test_mock_embed_differs_for_different_text() -> None:
    provider = MockEmbeddingProvider(dimension=8)

    vectors = await provider.embed(["hello", "goodbye"])

    assert vectors[0] != vectors[1]


async def test_mock_embed_records_embedded_texts() -> None:
    provider = MockEmbeddingProvider()

    await provider.embed(["a", "b"])
    await provider.embed(["c"])

    assert provider.embedded_texts == ["a", "b", "c"]


async def test_mock_embed_empty_list_returns_empty_list() -> None:
    provider = MockEmbeddingProvider()

    assert await provider.embed([]) == []
    assert provider.embedded_texts == []


async def test_mock_embed_raises_the_configured_failure() -> None:
    failure = EmbeddingProviderUnavailable("simulated outage")
    provider = MockEmbeddingProvider(failure=failure)

    with pytest.raises(EmbeddingProviderUnavailable):
        await provider.embed(["hello"])


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_embed_parses_vectors_in_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"

        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2, 0.3]},
                    {"embedding": [0.4, 0.5, 0.6]},
                ]
            },
        )

    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=3,
        client=_client_returning(handler),
    )

    vectors = await provider.embed(["first", "second"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


async def test_openai_embed_empty_list_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not have made a request")

    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=3,
        client=_client_returning(handler),
    )

    assert await provider.embed([]) == []


async def test_openai_embed_non_200_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    provider = OpenAIEmbeddingProvider(
        api_key="bad-key",
        model="text-embedding-3-small",
        dimension=3,
        client=_client_returning(handler),
    )

    with pytest.raises(EmbeddingProviderUnavailable):
        await provider.embed(["hello"])


async def test_openai_embed_timeout_raises_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=3,
        client=_client_returning(handler),
    )

    with pytest.raises(EmbeddingProviderTimeout):
        await provider.embed(["hello"])


async def test_openai_embed_wrong_vector_count_raises_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=3,
        client=_client_returning(handler),
    )

    with pytest.raises(EmbeddingDimensionMismatch):
        await provider.embed(["first", "second"])


async def test_openai_embed_wrong_vector_length_raises_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        model="text-embedding-3-small",
        dimension=3,
        client=_client_returning(handler),
    )

    with pytest.raises(EmbeddingDimensionMismatch):
        await provider.embed(["hello"])
