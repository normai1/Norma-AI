import pytest

from app.conversation import Message
from app.llm import LLMProviderUnavailable
from app.mock_llm import MockLLM


async def test_mock_llm_yields_the_scripted_response_in_order() -> None:
    llm = MockLLM(response="Sure, we can help with that today.", chunk_words=3)

    chunks = [
        chunk
        async for chunk in llm.stream(
            [Message(role="user", content="Can you help?")],
            system="You are helpful.",
            temperature=0.3,
        )
    ]

    assert "".join(chunks) == "Sure, we can help with that today."
    assert len(chunks) > 1


async def test_mock_llm_records_what_it_was_called_with() -> None:
    llm = MockLLM(response="Hello.")
    messages = [Message(role="user", content="Hi")]

    async for _ in llm.stream(messages, system="Be nice.", temperature=0.7):
        pass

    assert llm.received_messages == messages
    assert llm.received_system == "Be nice."
    assert llm.received_temperature == 0.7


async def test_mock_llm_raises_the_failure_after_the_scripted_text_is_exhausted() -> None:
    failure = LLMProviderUnavailable("boom")
    llm = MockLLM(response="Partial reply", failure=failure)

    chunks = []

    with pytest.raises(LLMProviderUnavailable):
        async for chunk in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
        ):
            chunks.append(chunk)

    assert "".join(chunks) == "Partial reply"


async def test_mock_llm_with_empty_response_yields_nothing() -> None:
    llm = MockLLM(response="")

    chunks = [
        chunk
        async for chunk in llm.stream(
            [Message(role="user", content="Hi")], system="Be nice.", temperature=0.3
        )
    ]

    assert chunks == []
