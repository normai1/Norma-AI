"""
LLM provider contract for the realtime turn loop (item 20d). Lives in
apps/voice, not norma_shared: apps/worker (post-call summaries, item 38) has
no LLM need yet, so there is no second consumer to justify a shared package
- mirrors item 20b's own "moved here, not duplicated, when a real
cross-service need first arises" rule. Shape deliberately mirrors
norma_shared.speech's error hierarchy for consistency across both provider
families.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from norma_shared.token_cost import TokenUsage

from app.conversation import Message

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderTimeout",
    "LLMProviderUnavailable",
    "LLMRateLimited",
    "Message",
    "TokenUsage",
]


class LLMProviderError(Exception):
    """
    Base class for an LLM provider's own failures, distinct from a bug in
    the calling code.
    """


class LLMProviderTimeout(LLMProviderError):
    """
    The provider did not respond within the caller's bound.
    """


class LLMProviderUnavailable(LLMProviderError):
    """
    The provider rejected the request, or the connection could not be
    established - auth failure or outage.
    """


class LLMRateLimited(LLMProviderError):
    """
    The provider refused this request because the caller is over its rate
    limit, not because anything is wrong.

    Separated from LLMProviderUnavailable because the two need opposite
    responses and the difference was invisible while both were "unavailable":
    an outage is worth retrying immediately, a per-minute token quota is the
    one thing retrying immediately cannot fix. Observed on a real call -
    three attempts inside eight seconds against a quota that resets after
    sixty, each one adding to the very budget it was waiting on.

    retry_after_seconds is what the provider said, when it said anything.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLMProvider(Protocol):
    """
    Streaming chat completion. The model is bound at construction (fixed
    per realtime/post-call tier); temperature is per-call since it varies
    by assistant (AssistantVersion.creativity), the same distinction
    ElevenLabsTTS's per-call voice_id draws against its construction-time
    model_id.
    """

    def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        """
        Stream a reply to messages (user/assistant turns only - system is
        passed separately, matching Anthropic's Messages API shape),
        yielding text deltas in order as they become available.
        """
        ...

    def last_usage(self) -> TokenUsage | None:
        """
        Token counts for the most recently completed stream(), or None if
        the provider did not report them (item 25b).

        Read *after* the stream is exhausted, not alongside it: both SDKs in
        use deliver usage only at the end - Groq on the final chunk, in
        `x_groq.usage`, and Anthropic through `get_final_message()`. A
        method rather than a per-delta yield because the stream's element
        type is the caller's text and must stay that way; this mirrors
        apps/api's `LLMProvider.last_token_budget()`, which reads a rate
        limit off the same kind of trailing metadata.

        Deliberately last-call state on a per-session provider instance,
        which is safe only because one session's turns are strictly
        sequential - a turn's stream is fully consumed, or abandoned, before
        the next one starts. A provider instance shared across concurrent
        calls would need this keyed per call instead.
        """
        ...
