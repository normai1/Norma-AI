"""
Groq implementation of app/llm.py's LLMProvider contract. API shape verified
against the installed groq SDK directly while building this feature:
client.chat.completions.create(..., stream=True) is OpenAI-compatible,
returning an async iterator of ChatCompletionChunk whose
choices[0].delta.content is str | None (the first chunk is often a
role-only delta with content=None). groq.APITimeoutError is a subclass of
groq.APIConnectionError, itself a subclass of the GroqError base every
other SDK failure (auth, rate limit, outage) also derives from - the same
hierarchy shape app/anthropic_llm.py already documents for the Anthropic
SDK.
"""

from collections.abc import AsyncIterator, Sequence

import groq

from app.conversation import Message
from app.llm import LLMProviderTimeout, LLMProviderUnavailable

# Matches app/anthropic_llm.py's own precedent exactly - a starting value
# for a conversational spoken reply, not a tuned product decision.
_DEFAULT_MAX_TOKENS = 300


class GroqLLM:
    """
    Accepts an injected client for testing (a small fake matching only the
    .chat.completions.create(...) surface used, never the real API); when
    none is given, a real groq.AsyncGroq is constructed and held for the
    life of this provider instance.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: groq.AsyncGroq | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = client or groq.AsyncGroq(api_key=api_key)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=temperature,
                stream=True,
                messages=[
                    {"role": "system", "content": system},
                    *[
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                ],
            )

            async for chunk in response:
                content = chunk.choices[0].delta.content

                if content:
                    yield content
        except groq.APITimeoutError as exc:
            raise LLMProviderTimeout("Groq request timed out") from exc
        except groq.GroqError as exc:
            raise LLMProviderUnavailable("Groq request failed") from exc
