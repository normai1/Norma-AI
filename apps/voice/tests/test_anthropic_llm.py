from typing import Self

import anthropic
import httpx2
import pytest

from app.anthropic_llm import AnthropicLLM
from app.conversation import Message
from app.llm import LLMProviderTimeout, LLMProviderUnavailable

_FAKE_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


async def _fake_text_stream(chunks, failure):
    for chunk in chunks:
        yield chunk

    if failure is not None:
        raise failure


class _FakeMessageStream:
    def __init__(self, chunks, *, failure: Exception | None = None) -> None:
        self.text_stream = _fake_text_stream(chunks, failure)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeMessages:
    def __init__(self, chunks: list[str], *, failure: Exception | None = None) -> None:
        self._chunks = chunks
        self._failure = failure
        self.received_kwargs: dict | None = None

    def stream(self, **kwargs) -> _FakeMessageStream:
        self.received_kwargs = kwargs

        return _FakeMessageStream(self._chunks, failure=self._failure)


class _FakeAnthropicClient:
    def __init__(self, chunks: list[str] = (), *, failure: Exception | None = None) -> None:
        self.messages = _FakeMessages(list(chunks), failure=failure)


async def test_stream_yields_the_clients_scripted_text_deltas() -> None:
    client = _FakeAnthropicClient(chunks=["Hello", " there."])
    llm = AnthropicLLM(api_key="fake", model="claude-haiku-4-5-20251001", client=client)

    chunks = [
        chunk
        async for chunk in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.4
        )
    ]

    assert "".join(chunks) == "Hello there."


async def test_stream_passes_model_system_temperature_and_messages_through() -> None:
    client = _FakeAnthropicClient(chunks=["Hi"])
    llm = AnthropicLLM(api_key="fake", model="claude-haiku-4-5-20251001", client=client)

    async for _ in llm.stream(
        [Message(role="user", content="What are your hours?")],
        system="You are helpful.",
        temperature=0.6,
    ):
        pass

    assert client.messages.received_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert client.messages.received_kwargs["system"] == "You are helpful."
    assert client.messages.received_kwargs["temperature"] == 0.6
    assert client.messages.received_kwargs["messages"] == [
        {"role": "user", "content": "What are your hours?"}
    ]


async def test_stream_maps_a_timeout_to_llm_provider_timeout() -> None:
    client = _FakeAnthropicClient(
        chunks=["partial"], failure=anthropic.APITimeoutError(request=_FAKE_REQUEST)
    )
    llm = AnthropicLLM(api_key="fake", model="claude-haiku-4-5-20251001", client=client)

    with pytest.raises(LLMProviderTimeout):
        async for _ in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
        ):
            pass


async def test_stream_maps_another_anthropic_error_to_llm_provider_unavailable() -> None:
    client = _FakeAnthropicClient(
        chunks=[], failure=anthropic.APIConnectionError(request=_FAKE_REQUEST)
    )
    llm = AnthropicLLM(api_key="fake", model="claude-haiku-4-5-20251001", client=client)

    with pytest.raises(LLMProviderUnavailable):
        async for _ in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
        ):
            pass
