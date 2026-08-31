"""
LLM provider contract for the realtime turn loop (item 20d). Lives in
apps/voice, not norma_shared: apps/worker (post-call summaries, item 36) has
no LLM need yet, so there is no second consumer to justify a shared package
- mirrors item 20b's own "moved here, not duplicated, when a real
cross-service need first arises" rule. Shape deliberately mirrors
norma_shared.speech's error hierarchy for consistency across both provider
families.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from app.conversation import Message

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderTimeout",
    "LLMProviderUnavailable",
    "Message",
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
    established - auth failure, outage, or rate limit.
    """


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
