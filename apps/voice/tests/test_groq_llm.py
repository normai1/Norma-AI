import groq
import httpx
import pytest

from app.conversation import Message
from app.groq_llm import GroqLLM
from app.llm import LLMProviderTimeout, LLMProviderUnavailable

_FAKE_REQUEST = httpx.Request(
    "POST", "https://api.groq.com/openai/v1/chat/completions"
)


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeStream:
    """
    Mimics groq.AsyncStream's async iteration surface - the first chunk is
    a role-only delta with content=None, matching the real API's own shape,
    to prove the None-content guard actually does something.
    """

    def __init__(self, chunks: list[str], *, failure: Exception | None = None) -> None:
        self._contents: list[str | None] = [None, *chunks]
        self._failure = failure

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._contents:
            return _FakeChunk(self._contents.pop(0))

        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure

        raise StopAsyncIteration


class _FakeCompletions:
    def __init__(self, chunks: list[str], *, failure: Exception | None = None) -> None:
        self._chunks = chunks
        self._failure = failure
        self.received_kwargs: dict | None = None

    async def create(self, **kwargs) -> _FakeStream:
        self.received_kwargs = kwargs

        return _FakeStream(self._chunks, failure=self._failure)


class _FakeChat:
    def __init__(self, chunks: list[str], *, failure: Exception | None = None) -> None:
        self.completions = _FakeCompletions(chunks, failure=failure)


class _FakeGroqClient:
    def __init__(self, chunks: list[str] = (), *, failure: Exception | None = None) -> None:
        self.chat = _FakeChat(list(chunks), failure=failure)


async def test_stream_yields_the_clients_scripted_text_deltas() -> None:
    client = _FakeGroqClient(chunks=["Hello", " there."])
    llm = GroqLLM(api_key="fake", model="llama-3.1-8b-instant", client=client)

    chunks = [
        chunk
        async for chunk in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.4
        )
    ]

    assert "".join(chunks) == "Hello there."


async def test_stream_passes_model_system_temperature_and_messages_through() -> None:
    client = _FakeGroqClient(chunks=["Hi"])
    llm = GroqLLM(api_key="fake", model="llama-3.1-8b-instant", client=client)

    async for _ in llm.stream(
        [Message(role="user", content="What are your hours?")],
        system="You are helpful.",
        temperature=0.6,
    ):
        pass

    assert client.chat.completions.received_kwargs["model"] == "llama-3.1-8b-instant"
    assert client.chat.completions.received_kwargs["temperature"] == 0.6
    assert client.chat.completions.received_kwargs["stream"] is True
    assert client.chat.completions.received_kwargs["messages"] == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What are your hours?"},
    ]


async def test_stream_maps_a_timeout_to_llm_provider_timeout() -> None:
    client = _FakeGroqClient(
        chunks=["partial"], failure=groq.APITimeoutError(request=_FAKE_REQUEST)
    )
    llm = GroqLLM(api_key="fake", model="llama-3.1-8b-instant", client=client)

    with pytest.raises(LLMProviderTimeout):
        async for _ in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
        ):
            pass


async def test_stream_maps_another_groq_error_to_llm_provider_unavailable() -> None:
    client = _FakeGroqClient(
        chunks=[], failure=groq.APIConnectionError(request=_FAKE_REQUEST)
    )
    llm = GroqLLM(api_key="fake", model="llama-3.1-8b-instant", client=client)

    with pytest.raises(LLMProviderUnavailable):
        async for _ in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
        ):
            pass
