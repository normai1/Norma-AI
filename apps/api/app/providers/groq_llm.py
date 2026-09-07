"""
Groq implementation of llm.py's contract, via its OpenAI-compatible chat
completions endpoint. httpx-based, matching openai_embedding.py's shape.
"""

import httpx

from app.providers.llm import LLMProviderTimeout, LLMProviderUnavailable

_BASE_URL = "https://api.groq.com"
_DEFAULT_TIMEOUT_SECONDS = 60.0


def _raise_for_http_status(status_code: int) -> None:
    """
    Map any non-2xx response onto this module's error hierarchy, mirroring
    openai_embedding.py's _raise_for_http_status - auth failure, rate
    limit, and outage are all a 4xx/5xx here too.
    """

    if not (200 <= status_code < 300):
        raise LLMProviderUnavailable(
            f"Groq chat completions request failed with status {status_code}",
        )


class GroqLLMProvider:
    """
    Generates text via Groq's /openai/v1/chat/completions endpoint.

    Accepts an injected httpx.AsyncClient for testing (MockTransport); when
    none is given, a client is created and closed per call, matching
    OpenAIEmbeddingProvider's exact lifecycle-management precedent.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = _BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None

        try:
            try:
                response = await client.post(
                    f"{self._base_url}/openai/v1/chat/completions",
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                    },
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise LLMProviderTimeout(
                    "Groq chat completions request timed out",
                ) from exc
            except httpx.TransportError as exc:
                raise LLMProviderUnavailable(
                    "Groq chat completions connection failed",
                ) from exc

            _raise_for_http_status(response.status_code)

            return response.json()["choices"][0]["message"]["content"]
        finally:
            if owns_client:
                await client.aclose()
