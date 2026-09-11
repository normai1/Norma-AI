"""
An allowance that will not come back today must stop the document, not be
waited out window by window.

Found live, and it is why a 50-page upload produced no FAQs at all: Groq's
tokens-per-day allowance of 200,000 was spent (199,529 used) and generation
settled in to "waiting 1169.0s as the provider asked" - per window, on a
ten-window document, for a quota that would not refill until the next day.
The per-minute headers alongside it reported a completely full bucket, 8,000
of 8,000 remaining resetting in 1ms, because the exhausted allowance appears
in no x-ratelimit header at all. Only the error body names it.
"""

import json
import logging
import uuid

import httpx
import pytest

from app.providers.groq_llm import GroqLLMProvider
from app.providers.llm import LLMRateLimited
from app.services.faq_generation import (
    LLMQuotaExhausted,
    _generate_for_window,
)

_TPD_BODY = {
    "error": {
        "message": (
            "Rate limit reached for model `openai/gpt-oss-120b` in "
            "organization `org_x` service tier `on_demand` on tokens per day "
            "(TPD): Limit 200000, Used 199529, Requested 559. Please try "
            "again in 38.016s."
        ),
        "type": "tokens",
        "code": "rate_limit_exceeded",
    }
}

_TPM_BODY = {
    "error": {
        "message": (
            "Rate limit reached for model `openai/gpt-oss-120b` on tokens "
            "per minute (TPM): Limit 8000, Used 7800, Requested 559."
        ),
        "type": "tokens",
        "code": "rate_limit_exceeded",
    }
}


def _provider_returning(body: dict, headers: dict[str, str]) -> GroqLLMProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=body, headers=headers)

    return GroqLLMProvider(
        api_key="key",
        model="a-model",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_a_per_day_limit_is_named_as_such() -> None:
    provider = _provider_returning(
        _TPD_BODY,
        # Exactly what the real response carried: a completely healthy
        # per-minute bucket next to an exhausted daily one.
        {
            "retry-after": "39",
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "8000",
            "x-ratelimit-reset-tokens": "1ms",
        },
    )

    with pytest.raises(LLMRateLimited) as caught:
        await provider.generate(system_prompt="s", user_prompt="u")

    assert caught.value.exhausted_window == "day"


async def test_a_per_minute_limit_is_named_as_such() -> None:
    provider = _provider_returning(_TPM_BODY, {"retry-after": "12"})

    with pytest.raises(LLMRateLimited) as caught:
        await provider.generate(system_prompt="s", user_prompt="u")

    assert caught.value.exhausted_window == "minute"


async def test_an_unrecognised_message_names_nothing_rather_than_guessing() -> None:
    provider = _provider_returning({"error": {"message": "slow down"}}, {})

    with pytest.raises(LLMRateLimited) as caught:
        await provider.generate(system_prompt="s", user_prompt="u")

    assert caught.value.exhausted_window is None


async def test_a_daily_limit_stops_the_window_instead_of_waiting() -> None:
    """
    The headline behaviour. Waiting here is not patience, it is denial: the
    allowance does not refill until tomorrow.
    """

    class _DailyLimited:
        async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
            raise LLMRateLimited(
                "429", retry_after_seconds=38.0, exhausted_window="day"
            )

    with pytest.raises(LLMQuotaExhausted) as caught:
        await _generate_for_window(
            _DailyLimited(),
            "document text",
            already_asked=[],
            knowledge_source_id=uuid.uuid4(),
        )

    assert caught.value.exhausted_window == "day"


async def test_an_absurdly_long_wait_stops_even_when_unnamed() -> None:
    """
    A provider that does not say which allowance ran out, but asks for
    nineteen minutes, is telling us the same thing.
    """

    class _LongWait:
        async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
            raise LLMRateLimited("429", retry_after_seconds=1169.0)

    with pytest.raises(LLMQuotaExhausted):
        await _generate_for_window(
            _LongWait(),
            "document text",
            already_asked=[],
            knowledge_source_id=uuid.uuid4(),
        )


async def test_a_short_per_minute_wait_is_still_waited_out() -> None:
    """
    The behaviour that must survive: a minute's allowance comes back, and
    waiting for it is how a long document gets covered in full.
    """

    attempts = {"n": 0}

    class _BrieflyLimited:
        async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
            attempts["n"] += 1

            if attempts["n"] == 1:
                raise LLMRateLimited(
                    "429", retry_after_seconds=0.0, exhausted_window="minute"
                )

            return json.dumps([{"question": "Q?", "answer": "A."}])

    pairs = await _generate_for_window(
        _BrieflyLimited(),
        "document text",
        already_asked=[],
        knowledge_source_id=uuid.uuid4(),
    )

    assert pairs == [("Q?", "A.")]
    assert attempts["n"] == 2


async def test_stopping_early_keeps_what_earlier_windows_produced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A partial FAQ list is worth having. Discarding it would mean the
    allowance was spent for nothing.
    """

    from app.services import faq_generation

    calls = {"n": 0}

    class _GoodThenExhausted:
        async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
            calls["n"] += 1

            if calls["n"] <= 2:
                return json.dumps(
                    [{"question": f"Q{calls['n']}?", "answer": f"A{calls['n']}."}]
                )

            raise LLMRateLimited(
                "429", retry_after_seconds=1169.0, exhausted_window="day"
            )

    text = " ".join(f"word{i}" for i in range(20_000))
    windows = faq_generation._windows(text)

    assert len(windows) > 3, "the fixture needs more windows than the provider serves"

    pair_groups = []
    already: list[str] = []

    with caplog.at_level(logging.WARNING):
        for window in windows:
            try:
                pairs = await faq_generation._generate_for_window(
                    _GoodThenExhausted(),
                    window,
                    already_asked=already,
                    knowledge_source_id=uuid.uuid4(),
                )
            except LLMQuotaExhausted:
                break

            pair_groups.append(pairs)
            already.extend(q for q, _a in pairs)

    assert len(pair_groups) == 2
    assert already == ["Q1?", "Q2?"]
