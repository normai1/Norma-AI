import pytest

from app.anthropic_llm import AnthropicLLM
from app.groq_llm import GroqLLM
from app.llm_provider_factory import (
    MissingAnthropicApiKeyError,
    MissingGroqApiKeyError,
    UnknownLLMProviderError,
    get_llm_provider,
)
from app.mock_llm import MockLLM


def test_default_provider_is_mock() -> None:
    assert isinstance(get_llm_provider(), MockLLM)


def test_explicit_mock_provider() -> None:
    assert isinstance(get_llm_provider("mock"), MockLLM)


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownLLMProviderError):
        get_llm_provider("not-a-real-provider")


def test_anthropic_without_api_key_raises() -> None:
    with pytest.raises(MissingAnthropicApiKeyError):
        get_llm_provider("anthropic")


def test_anthropic_with_api_key_constructs_the_real_provider(monkeypatch) -> None:
    monkeypatch.setattr("app.llm_provider_factory.config.ANTHROPIC_API_KEY", "fake-key")

    assert isinstance(get_llm_provider("anthropic"), AnthropicLLM)


def test_groq_without_api_key_raises() -> None:
    with pytest.raises(MissingGroqApiKeyError):
        get_llm_provider("groq")


def test_groq_with_api_key_constructs_the_real_provider(monkeypatch) -> None:
    monkeypatch.setattr("app.llm_provider_factory.config.GROQ_API_KEY", "fake-key")

    assert isinstance(get_llm_provider("groq"), GroqLLM)
