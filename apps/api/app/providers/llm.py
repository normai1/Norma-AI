"""
Text-generation LLM provider contract, for apps/api's own background/batch
uses (e.g. generating candidate FAQ entries from ingested knowledge source
content) - distinct from apps/voice's own realtime LLM providers, which
serve the live per-turn call loop under a completely different latency
budget (CLAUDE.md section 5.1: two planes, no cross-plane coupling).
"""

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


class LLMProvider(Protocol):
    """
    One-shot text generation: a system instruction plus a user prompt in,
    the model's full text response out.
    """

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """
        Generate a single text completion. Raises LLMProviderTimeout or
        LLMProviderUnavailable on failure - never returns empty output in
        place of a real error.
        """
        ...
