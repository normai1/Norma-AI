"""
Deterministic LLM mock. No network, no real model - mirrors
norma_shared.mock_speech.MockSTT's exact scripting/recording precedent so
the test suite never depends on a paid or live external API.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from norma_shared.token_cost import TokenUsage

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
        usage: TokenUsage | None = None,
    ) -> None:
        self._response = response
        self._chunk_words = chunk_words
        self._chunk_delay_seconds = chunk_delay_seconds
        self._failure = failure
        # What a real provider would report once the stream completes. None
        # - the default - is the provider that reports nothing, which is a
        # case the cost path has to handle rather than treat as zero.
        self._usage = usage
        self._last_usage: TokenUsage | None = None

        # Records the most recent stream() call's arguments, for a test to
        # assert what actually reached the provider - mirrors
        # MockSTT.received_keywords's exact precedent.
        self.received_messages: list[Message] | None = None
        self.received_system: str | None = None
        self.received_temperature: float | None = None
        # How many times stream() has been called - item 20g's retry tests
        # assert on this directly, rather than needing a stateful mock that
        # behaves differently per call (see this feature's spec).
        self.call_count = 0

    def last_usage(self) -> TokenUsage | None:
        return self._last_usage

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        self.call_count += 1
        self.received_messages = list(messages)
        self.received_system = system
        self.received_temperature = temperature
        # Mirrors the real providers: cleared on entry, set only once the
        # stream has actually run to completion, so an abandoned or failed
        # turn reports none.
        self._last_usage = None

        words = self._response.split(" ") if self._response else []

        for start in range(0, len(words), self._chunk_words):
            if self._chunk_delay_seconds:
                await asyncio.sleep(self._chunk_delay_seconds)

            chunk_words = words[start : start + self._chunk_words]
            is_last_chunk = start + self._chunk_words >= len(words)

            yield " ".join(chunk_words) + ("" if is_last_chunk else " ")

        if self._failure is not None:
            raise self._failure

        self._last_usage = self._usage
