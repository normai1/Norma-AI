"""
Item 25b: how long each provider took, and how it failed.

CLAUDE.md section 27 asks for "provider errors and timeouts by provider".
The three things worth pinning down are that a failure is loud, that a
barge-in is not mistaken for one, and that nothing the provider said about
the request ends up in the log - a provider error's message routinely quotes
the request body, which is the caller's own words.
"""

import asyncio
import logging

import pytest
from norma_shared.provider_telemetry import provider_call


def test_a_successful_call_records_the_provider_and_how_long_it_took(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with provider_call("groq", "llm.stream"):
        pass

    assert "provider=groq" in caplog.text
    assert "operation=llm.stream" in caplog.text
    assert "duration_ms=" in caplog.text


def test_success_stays_at_debug_so_a_healthy_call_does_not_flood_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A healthy turn makes several provider calls. At INFO they would bury
    everything an operator actually opens the log to find.
    """

    caplog.set_level(logging.DEBUG)

    with provider_call("elevenlabs", "tts.synthesize"):
        pass

    assert [record.levelno for record in caplog.records] == [logging.DEBUG]


def test_a_failure_is_a_warning_naming_the_providers_own_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class RateLimitError(RuntimeError):
        pass

    caplog.set_level(logging.DEBUG)

    with pytest.raises(RateLimitError), provider_call("groq", "llm.stream"):
        raise RateLimitError("rate limited")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]

    assert len(warnings) == 1
    assert "error=RateLimitError" in warnings[0].getMessage()


def test_the_error_message_itself_is_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    The reason only the type name is recorded. An httpx or SDK error quotes
    the request that caused it, and this request's body is what the caller
    just said (CLAUDE.md section 27 - never log transcript text).
    """

    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError), provider_call("groq", "llm.stream"):
        raise RuntimeError("failed on input: can I book for Tuesday at four")

    assert "Tuesday" not in caplog.text
    assert "book" not in caplog.text


def test_the_exception_reaches_the_caller_unchanged() -> None:
    """
    This observes; it never handles. The provider adapters above it depend
    on catching their own SDK's exception type.
    """

    original = ValueError("the original")

    with pytest.raises(ValueError) as raised, provider_call("groq", "llm.stream"):
        raise original

    assert raised.value is original


@pytest.mark.parametrize("abandonment", [GeneratorExit, asyncio.CancelledError])
def test_abandoning_a_call_is_not_a_provider_failure(
    caplog: pytest.LogCaptureFixture, abandonment: type[BaseException]
) -> None:
    """
    Every barge-in closes an abandoned LLM stream, and every first-token
    timeout cancels the task awaiting it. Counting either as a provider
    error would put a warning in the log for normal conversation and make
    the error telemetry useless.
    """

    caplog.set_level(logging.DEBUG)

    with pytest.raises(abandonment), provider_call("groq", "llm.stream"):
        raise abandonment()

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert "abandoned" in caplog.text
