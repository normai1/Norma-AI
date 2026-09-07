import json

import httpx
import pytest

from app.providers.groq_llm import GroqLLMProvider
from app.providers.llm import LLMProviderTimeout, LLMProviderUnavailable
from app.providers.mock_llm import MockLLMProvider


async def test_mock_generate_returns_the_configured_response() -> None:
    provider = MockLLMProvider(response="hello")

    result = await provider.generate(system_prompt="sys", user_prompt="usr")

    assert result == "hello"


async def test_mock_generate_defaults_to_an_empty_json_array() -> None:
    provider = MockLLMProvider()

    assert await provider.generate(system_prompt="sys", user_prompt="usr") == "[]"


async def test_mock_generate_records_every_call() -> None:
    provider = MockLLMProvider()

    await provider.generate(system_prompt="sys-a", user_prompt="usr-a")
    await provider.generate(system_prompt="sys-b", user_prompt="usr-b")

    assert provider.calls == [("sys-a", "usr-a"), ("sys-b", "usr-b")]


async def test_mock_generate_raises_the_configured_failure() -> None:
    failure = LLMProviderUnavailable("simulated outage")
    provider = MockLLMProvider(failure=failure)

    with pytest.raises(LLMProviderUnavailable):
        await provider.generate(system_prompt="sys", user_prompt="usr")


def _client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_groq_generate_returns_the_message_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.read())
        assert payload["model"] == "openai/gpt-oss-120b"
        assert payload["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]

        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "[]"}}]},
        )

    provider = GroqLLMProvider(
        api_key="test-key",
        model="openai/gpt-oss-120b",
        client=_client_returning(handler),
    )

    result = await provider.generate(system_prompt="sys", user_prompt="usr")

    assert result == "[]"


async def test_groq_generate_non_200_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    provider = GroqLLMProvider(
        api_key="bad-key",
        model="openai/gpt-oss-120b",
        client=_client_returning(handler),
    )

    with pytest.raises(LLMProviderUnavailable):
        await provider.generate(system_prompt="sys", user_prompt="usr")


async def test_groq_generate_timeout_raises_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    provider = GroqLLMProvider(
        api_key="test-key",
        model="openai/gpt-oss-120b",
        client=_client_returning(handler),
    )

    with pytest.raises(LLMProviderTimeout):
        await provider.generate(system_prompt="sys", user_prompt="usr")
