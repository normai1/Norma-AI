"""
Groq implementation of llm.py's contract, via its OpenAI-compatible chat
completions endpoint. httpx-based, matching huggingface_embedding.py's
shape.
"""

import re
from collections.abc import Mapping

import httpx

from app.providers.llm import (
    LLMProviderTimeout,
    LLMProviderUnavailable,
    LLMRateLimited,
    TokenBudget,
)

_BASE_URL = "https://api.groq.com"
_DEFAULT_TIMEOUT_SECONDS = 60.0


_RETRY_AFTER_HEADER = "retry-after"
_LIMIT_TOKENS_HEADER = "x-ratelimit-limit-tokens"
_REMAINING_TOKENS_HEADER = "x-ratelimit-remaining-tokens"
_RESET_TOKENS_HEADER = "x-ratelimit-reset-tokens"

_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$")


def _parse_seconds(value: str | None) -> float | None:
    """
    Groq writes reset times as durations - "42.285s", "11m31.2s" - not as
    plain numbers, while retry-after is plain seconds. Both shapes are
    accepted; anything unrecognised is None rather than a guess.
    """

    if not value:
        return None

    text = value.strip()

    try:
        return float(text)
    except ValueError:
        pass

    match = _DURATION.match(text)

    if match is None or not any(match.groups()):
        return None

    minutes, seconds = match.groups()

    return float(minutes or 0) * 60 + float(seconds or 0)


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None

    try:
        return int(value)
    except ValueError:
        return None


# Groq names the exhausted allowance only in the error body, e.g.
# "on tokens per day (TPD): Limit 200000, Used 199529". The x-ratelimit
# headers describe the per-minute bucket regardless of which one was
# actually hit, so they cannot be used to tell them apart.
_PER_DAY = re.compile(r"per\s+day|TPD|RPD", re.IGNORECASE)
_PER_MINUTE = re.compile(r"per\s+minute|TPM|RPM", re.IGNORECASE)


def _exhausted_window(body: object) -> str | None:
    """
    Which allowance the provider says ran out, from the error body.
    """

    if not isinstance(body, dict):
        return None

    error = body.get("error")
    message = error.get("message") if isinstance(error, dict) else None

    if not isinstance(message, str):
        return None

    if _PER_DAY.search(message):
        return "day"

    if _PER_MINUTE.search(message):
        return "minute"

    return None


def _raise_for_http_status(
    status_code: int, headers: Mapping[str, str], body: object = None
) -> None:
    """
    Map any non-2xx response onto this module's error hierarchy - auth
    failure, rate limit, and outage are all a 4xx/5xx here.

    429 is separated out because the response carries the answer to "when
    should I try again?", and guessing at it instead is what let three
    windows of a fifty-page document be abandoned while the provider was
    telling us exactly how long to wait.
    """

    if 200 <= status_code < 300:
        return

    if status_code == 429:
        window = _exhausted_window(body)

        raise LLMRateLimited(
            f"Groq chat completions request was rate limited (per {window})"
            if window
            else "Groq chat completions request was rate limited",
            retry_after_seconds=(
                _parse_seconds(headers.get(_RETRY_AFTER_HEADER))
                or _parse_seconds(headers.get(_RESET_TOKENS_HEADER))
            ),
            exhausted_window=window,
        )

    raise LLMProviderUnavailable(
        f"Groq chat completions request failed with status {status_code}",
    )


class GroqLLMProvider:
    """
    Generates text via Groq's /openai/v1/chat/completions endpoint.

    Accepts an injected httpx.AsyncClient for testing (MockTransport); when
    none is given, a client is created and closed per call, matching
    the same lifecycle-management precedent the embedding providers set.
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
        # What the last response said about the token allowance. Instance
        # state because the alternative is changing generate()'s return type
        # for every caller, and FAQ generation - the only caller that paces
        # itself - runs its windows one at a time, so there is no call whose
        # answer this could be confused with.
        self._last_token_budget: TokenBudget | None = None

    def last_token_budget(self) -> TokenBudget | None:
        return self._last_token_budget

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

            self._last_token_budget = TokenBudget(
                limit=_parse_int(response.headers.get(_LIMIT_TOKENS_HEADER)),
                remaining=_parse_int(response.headers.get(_REMAINING_TOKENS_HEADER)),
                reset_seconds=_parse_seconds(
                    response.headers.get(_RESET_TOKENS_HEADER)
                ),
            )

            try:
                body = response.json()
            except ValueError:
                body = None

            _raise_for_http_status(response.status_code, response.headers, body)

            return body["choices"][0]["message"]["content"]
        finally:
            if owns_client:
                await client.aclose()
