"""
LLM provider selection for apps/voice. Mirrors app/provider_factory.py's
exact shape and reasoning, scoped to the realtime tier only - a post-call
tier provider has no consumer yet (item 38, apps/worker, unbuilt).
"""

from app import config
from app.anthropic_llm import AnthropicLLM
from app.groq_llm import GroqLLM
from app.llm import LLMProvider
from app.mock_llm import MockLLM

_VALID_PROVIDER_NAMES = "'mock', 'anthropic', 'groq'"


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


class MissingGroqApiKeyError(ValueError):
    """
    The "groq" provider was selected but GROQ_API_KEY is unset. Fails at
    construction, not on the first stream() call - the same reasoning
    MissingAnthropicApiKeyError already established.
    """


def get_fallback_llm_provider() -> LLMProvider | None:
    """
    The provider to use when the configured one is rate limited, or None
    when no fallback is configured.

    Same provider, different model, because the token quota is per model -
    measured, not assumed: spending 1,500 tokens on gpt-oss-120b took its
    remaining allowance from 7,927 to 6,420 and left gpt-oss-20b's
    untouched at 7,927. A second model is therefore a second budget, which
    a retry against the first model can never be.
    """

    if not config.LLM_FALLBACK_MODEL:
        return None

    return get_llm_provider(model=config.LLM_FALLBACK_MODEL)


def get_llm_provider(name: str | None = None, *, model: str | None = None) -> LLMProvider:
    """
    Resolve an LLM provider by name, defaulting to LLM_PROVIDER, and to
    LLM_REALTIME_MODEL unless a model is named.
    """

    provider_name = name if name is not None else config.LLM_PROVIDER
    model_name = model if model is not None else config.LLM_REALTIME_MODEL

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
            model=model_name,
            base_url=config.ANTHROPIC_BASE_URL or None,
        )

    if provider_name == "groq":
        if not config.GROQ_API_KEY:
            raise MissingGroqApiKeyError(
                "GROQ_API_KEY is not set. The 'groq' LLM provider requires it.",
            )

        return GroqLLM(
            api_key=config.GROQ_API_KEY,
            model=model_name,
        )

    raise UnknownLLMProviderError(
        f"Unknown LLM_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_PROVIDER_NAMES}.",
    )
