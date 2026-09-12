from typing import Self

import anthropic
import httpx2
import pytest

from app.anthropic_llm import AnthropicLLM
from app.conversation import Message
from app.llm import LLMProviderTimeout, LLMProviderUnavailable, TokenUsage

_FAKE_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


async def _fake_text_stream(chunks, failure):
    for chunk in chunks:
        yield chunk

    if failure is not None:
        raise failure


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeFinalMessage:
    def __init__(self, usage: _FakeUsage | None) -> None:
        self.usage = usage


class _FakeMessageStream:
    def __init__(
        self,
        chunks,
        *,
        failure: Exception | None = None,
        usage: _FakeUsage | None = None,
    ) -> None:
        self.text_stream = _fake_text_stream(chunks, failure)
        self._usage = usage

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get_final_message(self) -> _FakeFinalMessage:
        """
        Item 25b. The real SDK assembles this from the stream's terminal
        events once text_stream is exhausted, which is the only point at
        which token usage exists.
        """

        return _FakeFinalMessage(self._usage)


class _FakeMessages:
    def __init__(
        self,
        chunks: list[str],
        *,
        failure: Exception | None = None,
        usage: _FakeUsage | None = None,
    ) -> None:
        self._chunks = chunks
        self._failure = failure
        self._usage = usage
        self.received_kwargs: dict | None = None

    def stream(self, **kwargs) -> _FakeMessageStream:
        self.received_kwargs = kwargs

        return _FakeMessageStream(
            self._chunks, failure=self._failure, usage=self._usage
        )


class _FakeAnthropicClient:
    def __init__(
        self,
        chunks: list[str] = (),
        *,
        failure: Exception | None = None,
        usage: _FakeUsage | None = None,
    ) -> None:
        self.messages = _FakeMessages(list(chunks), failure=failure, usage=usage)


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


async def test_stream_reports_the_tokens_the_final_message_carried() -> None:
    """
    Item 25b. Anthropic delivers usage through get_final_message(), not in
    the deltas, so it can only be read once the reply has finished.
    """

    client = _FakeAnthropicClient(
        chunks=["Hello"], usage=_FakeUsage(input_tokens=310, output_tokens=22)
    )
    llm = AnthropicLLM(
        api_key="fake", model="claude-haiku-4-5-20251001", client=client
    )

    async for _ in llm.stream(
        [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
    ):
        pass

    assert llm.last_usage() == TokenUsage(prompt_tokens=310, completion_tokens=22)


async def test_a_stream_abandoned_partway_reports_no_tokens() -> None:
    """
    A barge-in closes the generator before the reply finishes, so no final
    message is ever assembled. Recording the previous turn's usage, or a
    partial figure, would both be wrong - reporting nothing is the honest
    answer, and the one the cost path already handles.
    """

    client = _FakeAnthropicClient(
        chunks=["Hello", " there", " and", " welcome"],
        usage=_FakeUsage(input_tokens=310, output_tokens=22),
    )
    llm = AnthropicLLM(api_key="fake", model="claude-haiku-4-5-20251001", client=client)

    stream = llm.stream(
        [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
    )

    assert await stream.__anext__() == "Hello"

    await stream.aclose()

    assert llm.last_usage() is None


async def test_a_provider_that_reports_no_usage_is_not_an_error() -> None:
    client = _FakeAnthropicClient(chunks=["Hi"], usage=None)
    llm = AnthropicLLM(api_key="fake", model="claude-haiku-4-5-20251001", client=client)

    async for _ in llm.stream(
        [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
    ):
        pass

    assert llm.last_usage() is None
