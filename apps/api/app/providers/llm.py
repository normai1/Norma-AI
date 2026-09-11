"""
Text-generation LLM provider contract, for apps/api's own background/batch
uses (e.g. generating candidate FAQ entries from ingested knowledge source
content) - distinct from apps/voice's own realtime LLM providers, which
serve the live per-turn call loop under a completely different latency
budget (CLAUDE.md section 5.1: two planes, no cross-plane coupling).
"""

from dataclasses import dataclass
from typing import Protocol


class LLMProviderError(Exception):
    """
    Base class for a text-generation provider's own failures, distinct from
    a bug in the calling code.
    """


class LLMProviderTimeout(LLMProviderError):
    """
    The provider did not respond within the caller's bound.
    """


class LLMProviderUnavailable(LLMProviderError):
    """
    The provider rejected the request, or the connection could not be
    established - auth failure, outage, or rate limit.
    """


@dataclass(frozen=True)
class TokenBudget:
    """
    What a provider last said about the caller's token allowance.

    Provider-agnostic on purpose: every hosted LLM meters something per
    minute and most report it on the response, so this is the shape of the
    answer rather than one vendor's header names. A provider that says
    nothing returns None and callers fall back to configuration.

    The reason this exists at all: FAQ generation was pacing itself against a
    configured 39,000 tokens per minute while the account's real allowance
    for the model was 8,000. Three windows of a fifty-page document were
    refused and abandoned, which is the same "only generates 8 FAQs" symptom
    the pacing was added to fix. A number the provider tells us cannot drift
    from the truth the way a number in a settings file does.
    """

    limit: int | None = None
    remaining: int | None = None
    reset_seconds: float | None = None


class LLMRateLimited(LLMProviderError):
    """
    The provider refused this call because the caller is over its rate
    limit, and said how long to wait.

    Distinct from LLMProviderUnavailable because the response is different:
    an outage is retried hopefully with backoff, while this one carries the
    provider's own answer to "when?" and waiting exactly that long is both
    the fastest recovery and the one guaranteed not to earn another refusal.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        exhausted_window: str | None = None,
    ):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        # Which allowance ran out, when the provider says - "minute",
        # "day", or None if it does not.
        #
        # It matters because the two want opposite responses. A minute's
        # worth of tokens comes back in under a minute and waiting for it
        # finishes the document. A day's worth does not, and a caller that
        # treats them the same waits out a quota that will not clear until
        # tomorrow, one window at a time, having produced nothing.
        #
        # Found live: FAQ generation logged "waiting 1169.0s as the provider
        # asked" against a tokens-per-day limit of 200,000 with 199,529
        # used, while the per-minute headers alongside it reported a
        # completely full bucket - 8,000 of 8,000 remaining, resetting in
        # 1ms. The exhausted allowance appears in no x-ratelimit header at
        # all; only the error body names it.
        self.exhausted_window = exhausted_window


class LLMProvider(Protocol):
    """
    One-shot text generation: a system instruction plus a user prompt in,
    the model's full text response out.
    """

    def last_token_budget(self) -> TokenBudget | None:
        """
        What the provider said about the token allowance on the most recent
        call, or None if it says nothing.

        Read straight after generate() by a caller that is pacing itself, so
        it can pace against the real allowance rather than a configured
        guess. Deliberately per-provider-instance state rather than a return
        value: it would otherwise change generate()'s signature for every
        caller, almost none of which cares.
        """

        ...

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """
        Generate a single text completion. Raises LLMProviderTimeout or
        LLMProviderUnavailable on failure - never returns empty output in
        place of a real error.
        """
        ...
