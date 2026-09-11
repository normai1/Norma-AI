"""
The provider knows its own limit and says so on every response; pacing
against a number in a settings file instead is how three windows of a
fifty-page document came to be abandoned while the answer was "wait 42
seconds".

Observed on the real endpoint: x-ratelimit-limit-tokens: 8000, against a
configured 39,000.
"""

import httpx
import pytest

from app.providers.groq_llm import GroqLLMProvider, _parse_seconds
from app.providers.llm import LLMProviderUnavailable, LLMRateLimited

_BODY = {"choices": [{"message": {"content": "hi"}}]}


def _provider(handler) -> GroqLLMProvider:
    return GroqLLMProvider(
        api_key="key",
        model="a-model",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42.285s", 42.285),
        ("11m31.2s", 691.2),
        ("2", 2.0),
        ("1.5", 1.5),
        ("", None),
        ("bogus", None),
        (None, None),
    ],
)
def test_reset_durations_are_parsed(raw: str | None, expected: float | None) -> None:
    """
    Groq writes these as durations, not plain numbers - "42.285s",
    "11m31.2s" - while retry-after is plain seconds. Reading one as the
    other would wait 42 seconds as 42 minutes, or not at all.
    """

    assert _parse_seconds(raw) == expected


async def test_the_reported_token_budget_is_available_to_the_caller() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_BODY,
            headers={
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "2362",
                "x-ratelimit-reset-tokens": "42.285s",
            },
        )

    provider = _provider(handler)

    assert provider.last_token_budget() is None

    await provider.generate(system_prompt="s", user_prompt="u")
    budget = provider.last_token_budget()

    assert budget is not None
    assert budget.limit == 8000
    assert budget.remaining == 2362
    assert budget.reset_seconds == pytest.approx(42.285)


async def test_a_rate_limit_is_its_own_error_carrying_the_wait() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={}, headers={"retry-after": "7.5"})

    with pytest.raises(LLMRateLimited) as caught:
        await _provider(handler).generate(system_prompt="s", user_prompt="u")

    assert caught.value.retry_after_seconds == pytest.approx(7.5)


async def test_a_rate_limit_falls_back_to_the_reset_header() -> None:
    """
    retry-after is not always present; the token reset time answers the same
    question and is.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={}, headers={"x-ratelimit-reset-tokens": "42.285s"}
        )

    with pytest.raises(LLMRateLimited) as caught:
        await _provider(handler).generate(system_prompt="s", user_prompt="u")

    assert caught.value.retry_after_seconds == pytest.approx(42.285)


async def test_a_rate_limit_with_no_hint_still_raises_its_own_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(LLMRateLimited) as caught:
        await _provider(handler).generate(system_prompt="s", user_prompt="u")

    assert caught.value.retry_after_seconds is None


async def test_other_failures_are_still_unavailable_not_rate_limits() -> None:
    """
    A 500 is not a "wait and it will clear" situation, and treating it as
    one would wait out an outage instead of failing the window.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(LLMProviderUnavailable):
        await _provider(handler).generate(system_prompt="s", user_prompt="u")


async def test_a_response_without_the_headers_reports_nothing_rather_than_zero() -> (
    None
):
    """
    None means "the provider said nothing", and the configured budget then
    stands. Zeroes would read as "no allowance left" and stall generation
    forever.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_BODY)

    provider = _provider(handler)
    await provider.generate(system_prompt="s", user_prompt="u")
    budget = provider.last_token_budget()

    assert budget is not None
    assert budget.limit is None
    assert budget.remaining is None
