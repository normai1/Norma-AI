"""
HuggingFace implementation of embedding.py's contract, via the Inference
Providers router (https://router.huggingface.co) rather than a self-hosted
model - an in-process sentence-transformers model was evaluated and rejected
(see build-plan notes): the specific multilingual model requested had no
working hosted provider and crashed on CPU via its own custom remote code,
and even a healthy model's one-time load time on this environment's CPU was
far too slow to be a safe bet against CLAUDE.md's retrieval latency budget.
httpx-based, matching openai_embedding.py's shape exactly.
"""

import httpx

from app.providers.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingProviderTimeout,
    EmbeddingProviderUnavailable,
)

_BASE_URL = "https://router.huggingface.co"
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _raise_for_http_status(status_code: int) -> None:
    """
    Map any non-2xx response onto this module's error hierarchy, mirroring
    openai_embedding.py's _raise_for_http_status - auth failure, an
    unsupported/unavailable model on the router, rate limit, and outage are
    all a 4xx/5xx here too.
    """

    if not (200 <= status_code < 300):
        raise EmbeddingProviderUnavailable(
            f"HuggingFace embeddings request failed with status {status_code}",
        )


class HuggingFaceEmbeddingProvider:
    """
    Embeds text via a HuggingFace Inference Providers router model that
    serves the "feature-extraction" pipeline task, returning one pooled
    vector per input text.

    Accepts an injected httpx.AsyncClient for testing (MockTransport); when
    none is given, a client is created and closed per call, matching
    OpenAIEmbeddingProvider's exact lifecycle-management precedent.
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
                    f"{self._base_url}/hf-inference/models/{self._model}"
                    "/pipeline/feature-extraction",
                    json={"inputs": texts},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise EmbeddingProviderTimeout(
                    "HuggingFace embeddings request timed out",
                ) from exc
            except httpx.TransportError as exc:
                raise EmbeddingProviderUnavailable(
                    "HuggingFace embeddings connection failed",
                ) from exc

            _raise_for_http_status(response.status_code)

            vectors = response.json()

            if not isinstance(vectors, list) or len(vectors) != len(texts):
                got = len(vectors) if isinstance(vectors, list) else 0
                raise EmbeddingDimensionMismatch(
                    f"HuggingFace returned {got} embeddings for "
                    f"{len(texts)} input texts",
                )

            for vector in vectors:
                if not isinstance(vector, list) or len(vector) != self._dimension:
                    got = len(vector) if isinstance(vector, list) else 0
                    raise EmbeddingDimensionMismatch(
                        f"HuggingFace returned a {got}-dimension embedding, "
                        f"expected {self._dimension}",
                    )

            return vectors
        finally:
            if owns_client:
                await client.aclose()
