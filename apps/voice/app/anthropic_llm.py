"""
Anthropic implementation of app/llm.py's LLMProvider contract. API shape
verified against the installed anthropic SDK directly while building this
feature: client.messages.stream(...) is an async context manager exposing
.text_stream, and anthropic.APITimeoutError is a subclass of
anthropic.APIConnectionError, itself a subclass of the AnthropicError base
every other SDK failure (auth, rate limit, outage) also derives from.
"""

import logging
from collections.abc import AsyncIterator, Sequence

import anthropic
from norma_shared.provider_telemetry import provider_call
from norma_shared.token_cost import TokenUsage

from app.conversation import Message
from app.llm import LLMProviderTimeout, LLMProviderUnavailable

logger = logging.getLogger(__name__)

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
        self._last_usage: TokenUsage | None = None

    def last_usage(self) -> TokenUsage | None:
        return self._last_usage

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        # See GroqLLM.stream: cleared up front so a failed or abandoned turn
        # never reports the previous turn's tokens as its own.
        self._last_usage = None

        try:
            with provider_call("anthropic", "llm.stream"):
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

                    # Only reachable once text_stream is exhausted, which is
                    # the point: the SDK assembles the final message - usage
                    # included - from the stream's own terminal events, so
                    # there is nothing to read until the stream has ended.
                    # An abandoned stream (barge-in) never gets here and
                    # correctly reports no usage.
                    #
                    # Guarded because this is the audio path and the caller
                    # has already heard the whole reply by now: whatever this
                    # accounting read does, it must not be able to turn a
                    # delivered answer into a failed turn (CLAUDE.md section
                    # 41 - a defined fallback for every failure in the audio
                    # path). The fallback is no usage, which is already a
                    # case the cost path handles.
                    self._last_usage = await _final_usage(stream)
        except anthropic.APITimeoutError as exc:
            raise LLMProviderTimeout("Anthropic request timed out") from exc
        except anthropic.AnthropicError as exc:
            raise LLMProviderUnavailable("Anthropic request failed") from exc


async def _final_usage(stream: object) -> TokenUsage | None:
    """
    Token counts off a finished stream, or None if they cannot be read.

    Separated from the stream body so the guard is narrow: only the
    accounting read is tolerated failing, never the streaming of the reply
    itself.
    """

    get_final_message = getattr(stream, "get_final_message", None)

    if not callable(get_final_message):
        return None

    try:
        return _usage_of(await get_final_message())
    except Exception:
        logger.warning("could not read this turn's token usage from the stream")

        return None


def _usage_of(message: object) -> TokenUsage | None:
    """
    Token counts off a completed Anthropic message, or None if the shape is
    not what this expects.

    Attribute lookups rather than a typed unwrap, matching what the tests'
    small fakes can reasonably provide and staying tolerant of an SDK that
    adds fields (cache reads, for one) around the two this needs.
    """

    usage = getattr(message, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)

    if input_tokens is None or output_tokens is None:
        return None

    return TokenUsage(
        prompt_tokens=int(input_tokens), completion_tokens=int(output_tokens)
    )
