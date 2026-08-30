"""
Deterministic LLM mock. No network, no real model - mirrors
norma_shared.mock_speech.MockSTT's exact scripting/recording precedent so
the test suite never depends on a paid or live external API.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.conversation import Message
from app.llm import LLMProviderError


class MockLLM:
    def __init__(
        self,
        *,
        response: str = "",
        chunk_words: int = 3,
        chunk_delay_seconds: float = 0.0,
        failure: LLMProviderError | None = None,
    ) -> None:
        self._response = response
        self._chunk_words = chunk_words
        self._chunk_delay_seconds = chunk_delay_seconds
        self._failure = failure

        # Records the most recent stream() call's arguments, for a test to
        # assert what actually reached the provider - mirrors
        # MockSTT.received_keywords's exact precedent.
        self.received_messages: list[Message] | None = None
        self.received_system: str | None = None
        self.received_temperature: float | None = None

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        self.received_messages = list(messages)
        self.received_system = system
        self.received_temperature = temperature

        words = self._response.split(" ") if self._response else []

        for start in range(0, len(words), self._chunk_words):
            if self._chunk_delay_seconds:
                await asyncio.sleep(self._chunk_delay_seconds)

            chunk_words = words[start : start + self._chunk_words]
            is_last_chunk = start + self._chunk_words >= len(words)

            yield " ".join(chunk_words) + ("" if is_last_chunk else " ")

        if self._failure is not None:
            raise self._failure
