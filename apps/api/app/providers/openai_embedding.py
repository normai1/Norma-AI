"""
OpenAI implementation of embedding.py's contract. httpx-based rather than
the official SDK - httpx is already a first-class dependency here (the web
crawler uses it too), and one JSON POST needs no more than that.
"""

import httpx

from app.providers.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingProviderTimeout,
    EmbeddingProviderUnavailable,
)

_BASE_URL = "https://api.openai.com"
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _raise_for_http_status(status_code: int) -> None:
    """
    Map any non-2xx response onto this module's error hierarchy. Every
    OpenAI failure mode (auth, quota, rate limit, outage) is a 4xx/5xx, so a
    single range check covers the whole table without special-casing each
    code individually - the same reasoning ElevenLabsTTS's own
    _raise_for_http_status already uses.
    """

    if not (200 <= status_code < 300):
        raise EmbeddingProviderUnavailable(
            f"OpenAI embeddings request failed with status {status_code}",
        )


class OpenAIEmbeddingProvider:
    """
    Embeds text via OpenAI's /v1/embeddings endpoint.

    Accepts an injected httpx.AsyncClient for testing (MockTransport); when
    none is given, a client is created and closed per call, matching
    ElevenLabsTTS's exact lifecycle-management precedent.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimension: int,
        client: httpx.AsyncClient | None = None,
        base_url: str = _BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._client = client
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None

        try:
            try:
                response = await client.post(
                    f"{self._base_url}/v1/embeddings",
                    json={"model": self._model, "input": texts},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise EmbeddingProviderTimeout(
                    "OpenAI embeddings request timed out",
                ) from exc
            except httpx.TransportError as exc:
                raise EmbeddingProviderUnavailable(
                    "OpenAI embeddings connection failed",
                ) from exc

            _raise_for_http_status(response.status_code)

            data = response.json()["data"]

            if len(data) != len(texts):
                raise EmbeddingDimensionMismatch(
                    f"OpenAI returned {len(data)} embeddings for "
                    f"{len(texts)} input texts",
                )

            vectors = [item["embedding"] for item in data]

            for vector in vectors:
                if len(vector) != self._dimension:
                    raise EmbeddingDimensionMismatch(
                        f"OpenAI returned a {len(vector)}-dimension embedding, "
                        f"expected {self._dimension}",
                    )

            return vectors
        finally:
            if owns_client:
                await client.aclose()
