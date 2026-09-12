"""
Item 25b: how long each external provider took, and how it failed when it
did.

CLAUDE.md section 27 asks for "provider errors and timeouts by provider" and
per-leg latency; section 33's voice-debugging sequence is a list of provider
boundaries to isolate between. `TurnMetric` already answers "how long did the
LLM leg take on this turn" for the turns that completed - but it says nothing
about *which* provider was slow, records nothing at all for a call that
failed before the leg it would have timed, and covers only the four legs of
the turn loop. A speech provider that reconnects twice, an embedding call
that times out, a TTS request rejected for an unknown voice: all of those are
the answer to "why was that call bad", and none of them reach a TurnMetric row.

This module is the other half: one log line per provider call, carrying the
provider, the operation, the duration, and - on failure - the exception type.
Never the payload. A provider's request body is the caller's words and its
response is the assistant's reply (section 27 forbids both), and its URL and
headers carry credentials, which is exactly the leak `logging_setup` exists
to scrub. The type name is the diagnostic; the content never is.

Aggregation is deliberately left out. These lines are correlated by call and
turn already (`norma_shared.correlation`), so "which provider was slow on
this call" is a grep, and anything more - percentiles by provider, error
rates over time - is a query over rows, which is item 50's job (call
analytics), not a counter held in a voice worker's memory that dies with the
process.
"""

import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

__all__ = ["ProviderCall", "provider_call"]

logger = logging.getLogger(__name__)


# Leaving a provider call early is a normal event in this pipeline, not a
# provider failure, and recording it as one would make the error telemetry
# useless: every barge-in cancels an LLM stream mid-flight, and every
# first-token timeout cancels the task awaiting it. Both arrive here as an
# exception thrown *into* the wrapped code rather than raised by it -
# GeneratorExit when an abandoned async generator is closed, CancelledError
# when the task is cancelled - so they are separable from a real fault, and
# worth counting in their own right: how often callers interrupt is a
# product signal.
_ABANDONMENT = (GeneratorExit, asyncio.CancelledError)


@dataclass(frozen=True)
class ProviderCall:
    """
    One completed attempt against one provider.

    `error` is the exception's *type name*, never its message: an httpx or
    SDK error's message routinely quotes the request that caused it.
    """

    provider: str
    operation: str
    duration_ms: float
    error: str | None = None
    abandoned: bool = False

    @property
    def succeeded(self) -> bool:
        return self.error is None and not self.abandoned


@contextmanager
def provider_call(provider: str, operation: str) -> Iterator[None]:
    """
    Time one call to `provider` and log what happened.

    Success logs at DEBUG and failure at WARNING, which is the asymmetry the
    operator actually wants: a healthy call makes several provider calls per
    turn and would otherwise bury everything else in the log, while a failure
    is the line somebody is looking for. Abandonment (see _ABANDONMENT) is a
    third outcome, logged at DEBUG - it is normal, and counting it as an
    error would put a WARNING in the log for every barge-in. The exception is
    re-raised unchanged in every case - this observes, it never handles.
    Callers that translate SDK errors into Norma's own hierarchy should wrap
    *inside* their translation so the recorded type is the provider's own,
    which is the one worth knowing.

    Synchronous on purpose despite every caller being async: it wraps an
    `await`, it performs no I/O itself, and `contextlib.contextmanager`
    around a timer is the whole implementation. An async context manager
    here would add a coroutine per provider call to the audio path and buy
    nothing.
    """

    started = time.monotonic()

    def elapsed_ms() -> float:
        return (time.monotonic() - started) * 1000

    try:
        yield
    except _ABANDONMENT as exc:
        _log(
            ProviderCall(
                provider=provider,
                operation=operation,
                duration_ms=elapsed_ms(),
                error=type(exc).__name__,
                abandoned=True,
            )
        )

        raise
    except BaseException as exc:
        _log(
            ProviderCall(
                provider=provider,
                operation=operation,
                duration_ms=elapsed_ms(),
                error=type(exc).__name__,
            )
        )

        raise
    else:
        _log(
            ProviderCall(
                provider=provider, operation=operation, duration_ms=elapsed_ms()
            )
        )


def _log(call: ProviderCall) -> None:
    if call.succeeded:
        logger.debug(
            "provider call: provider=%s operation=%s duration_ms=%.1f",
            call.provider,
            call.operation,
            call.duration_ms,
        )

        return

    if call.abandoned:
        logger.debug(
            "provider call abandoned: provider=%s operation=%s "
            "duration_ms=%.1f after=%s",
            call.provider,
            call.operation,
            call.duration_ms,
            call.error,
        )

        return

    logger.warning(
        "provider call failed: provider=%s operation=%s duration_ms=%.1f error=%s",
        call.provider,
        call.operation,
        call.duration_ms,
        call.error,
    )
