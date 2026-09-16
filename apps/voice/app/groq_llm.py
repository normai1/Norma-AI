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

Item 25b's token counts come off the same stream. Verified against the
installed SDK rather than assumed: ChatCompletionChunk carries both a
top-level `usage` and an `x_groq` field, and it is `x_groq.usage` that
Groq populates on the final chunk of a stream, with prompt_tokens and
completion_tokens on a CompletionUsage. No request-side opt-in is needed
and none is available - unlike OpenAI proper, this SDK's create() has no
`stream_options` parameter at all, which is the first thing tried.
"""

import logging
from collections.abc import AsyncIterator, Sequence

import groq
from norma_shared.provider_telemetry import provider_call
from norma_shared.token_cost import TokenUsage

from app.conversation import Message
from app.llm import LLMProviderTimeout, LLMProviderUnavailable, LLMRateLimited

logger = logging.getLogger(__name__)

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
        self._last_usage: TokenUsage | None = None

    async def warm(self) -> None:
        """
        Open the connection before a caller needs it.

        The first request of a session pays DNS, TCP and TLS before a single
        token can stream back, and the turn metrics show what that costs:
        across 200 real turns the first turn of a call waited 1400ms for a
        first token against 883ms on later turns. Called at session start,
        while the greeting is playing.

        Listing models rather than sending a completion, deliberately: it
        opens the same connection and spends none of the realtime model's
        tokens-per-minute allowance, which is already the binding constraint
        on how many turns a conversation can have.

        Never raises - a warm that did not work leaves the call exactly where
        it would have been without it.
        """

        try:
            await self._client.models.list()
        except Exception:
            logger.debug("llm connection warm-up did not complete", exc_info=True)

    def last_usage(self) -> TokenUsage | None:
        return self._last_usage

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        # Cleared up front so a turn whose stream fails, or whose provider
        # sends no usage, reports nothing rather than the previous turn's
        # numbers - the one way this could silently produce a wrong cost.
        self._last_usage = None

        try:
            # Inside the translation, so the type recorded is the SDK's own
            # (APITimeoutError, RateLimitError) rather than Norma's two-way
            # collapse of it - see provider_call's docstring.
            with provider_call("groq", "llm.stream"):
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
                    usage = _usage_of(chunk)

                    if usage is not None:
                        self._last_usage = usage

                    # The final, usage-carrying chunk has an empty choices
                    # list on an OpenAI-compatible stream, so this cannot be
                    # read before the guard above.
                    if not chunk.choices:
                        continue

                    content = chunk.choices[0].delta.content

                    if content:
                        yield content
        except groq.APITimeoutError as exc:
            raise LLMProviderTimeout("Groq request timed out") from exc
        except groq.RateLimitError as exc:
            # Before the general GroqError clause, which it is a subclass of.
            # A quota refusal is not an outage and must not be retried the
            # same way - see LLMRateLimited.
            raise LLMRateLimited(
                "Groq refused the request: over the rate limit",
                retry_after_seconds=_retry_after_seconds(exc),
            ) from exc
        except groq.GroqError as exc:
            raise LLMProviderUnavailable("Groq request failed") from exc


def _retry_after_seconds(error: object) -> float | None:
    """
    How long Groq asked the caller to wait, or None if it did not say.

    Read defensively through getattr: the SDK exposes response headers on
    APIStatusError, but this is an accounting detail on an error path in the
    audio path, and a missing attribute must not turn a rate limit into a
    crash.
    """

    headers = getattr(getattr(error, "response", None), "headers", None)

    if headers is None:
        return None

    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def _usage_of(chunk: object) -> TokenUsage | None:
    """
    The token counts on one streamed chunk, from either place the SDK's
    model can carry them, or None for the overwhelming majority that carry
    neither.

    `x_groq.usage` is where a real Groq stream puts them; the top-level
    `usage` is the OpenAI-compatible field the same model also declares.
    Both are read because the fallback costs nothing and a provider moving
    to the standard field would otherwise silently stop reporting cost.
    """

    x_groq = getattr(chunk, "x_groq", None)

    for usage in (getattr(x_groq, "usage", None), getattr(chunk, "usage", None)):
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)

        if prompt_tokens is not None and completion_tokens is not None:
            return TokenUsage(
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
            )

    return None
