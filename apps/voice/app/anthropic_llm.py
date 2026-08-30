"""
Anthropic implementation of app/llm.py's LLMProvider contract. API shape
verified against the installed anthropic SDK directly while building this
feature: client.messages.stream(...) is an async context manager exposing
.text_stream, and anthropic.APITimeoutError is a subclass of
anthropic.APIConnectionError, itself a subclass of the AnthropicError base
every other SDK failure (auth, rate limit, outage) also derives from.
"""

from collections.abc import AsyncIterator, Sequence

import anthropic

from app.conversation import Message
from app.llm import LLMProviderTimeout, LLMProviderUnavailable

# A starting value for a conversational spoken reply, not a tuned product
# decision - matches app/turn_detection.py's own FALLBACK_TIMEOUT_SECONDS
# precedent for an unvalidated constant.
_DEFAULT_MAX_TOKENS = 300


class AnthropicLLM:
    """
    Accepts an injected client for testing (a small fake matching only the
    .messages.stream(...) surface used, never the real API); when none is
    given, a real anthropic.AsyncAnthropic is constructed and held for the
    life of this provider instance.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        client: anthropic.AsyncAnthropic | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                temperature=temperature,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APITimeoutError as exc:
            raise LLMProviderTimeout("Anthropic request timed out") from exc
        except anthropic.AnthropicError as exc:
            raise LLMProviderUnavailable("Anthropic request failed") from exc
