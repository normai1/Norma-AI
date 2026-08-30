"""
LLM provider selection for apps/voice. Mirrors app/provider_factory.py's
exact shape and reasoning, scoped to the realtime tier only - a post-call
tier provider has no consumer yet (item 35, apps/worker, unbuilt).
"""

from app import config
from app.anthropic_llm import AnthropicLLM
from app.llm import LLMProvider
from app.mock_llm import MockLLM

_VALID_PROVIDER_NAMES = "'mock', 'anthropic'"


class UnknownLLMProviderError(ValueError):
    """
    A configured LLM_PROVIDER name has no known implementation.
    """


class MissingAnthropicApiKeyError(ValueError):
    """
    The "anthropic" provider was selected but ANTHROPIC_API_KEY is unset.
    Fails at construction, not on the first stream() call - the same
    reasoning MissingElevenLabsApiKeyError already established.
    """


def get_llm_provider(name: str | None = None) -> LLMProvider:
    """
    Resolve an LLM provider by name, defaulting to LLM_PROVIDER.
    """

    provider_name = name if name is not None else config.LLM_PROVIDER

    if provider_name == "mock":
        return MockLLM()

    if provider_name == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise MissingAnthropicApiKeyError(
                "ANTHROPIC_API_KEY is not set. The 'anthropic' LLM "
                "provider requires it.",
            )

        return AnthropicLLM(
            api_key=config.ANTHROPIC_API_KEY,
            model=config.LLM_REALTIME_MODEL,
            base_url=config.ANTHROPIC_BASE_URL or None,
        )

    raise UnknownLLMProviderError(
        f"Unknown LLM_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_PROVIDER_NAMES}.",
    )
