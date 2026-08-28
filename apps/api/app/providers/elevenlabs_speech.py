"""
ElevenLabs implementations of 9a's speech provider contracts. Vendor API
shapes were verified against ElevenLabs' live documentation while writing
feature 9b's spec - see
blueprint/history/features/09b-elevenlabs-speech-adapters.md for the
verified request/response shapes and the reasoning behind the choices here.
"""

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets
import websockets.exceptions

from app.providers.speech import (
    SpeechProviderError,
    SpeechProviderTimeout,
    SpeechProviderUnavailable,
    TranscriptEvent,
    Voice,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.elevenlabs.io"
_WS_BASE_URL = "wss://api.elevenlabs.io"
_DEFAULT_MODEL_ID = "eleven_multilingual_v2"
_DEFAULT_TIMEOUT_SECONDS = 30.0

# A real catalogue is dozens to a few hundred voices at 100 per page; this is
# a backstop against an unbounded loop (CLAUDE.md section 37), not a limit
# expected to be hit.
_MAX_VOICE_PAGES = 20
_VOICES_PAGE_SIZE = 100


def _map_voice(raw: dict[str, Any]) -> Voice:
    """
    Map one ElevenLabs voice object onto 9a's Voice. gender and language live
    under labels, which ElevenLabs documents as optional and free-form -
    verified_languages' locale is the fallback for language, then "en".
    """

    labels = raw.get("labels") or {}
    language = labels.get("language")

    if not language:
        verified_languages = raw.get("verified_languages") or []

        if verified_languages:
            language = verified_languages[0].get("locale")

    return Voice(
        id=raw["voice_id"],
        name=raw.get("name", ""),
        language=language or "en",
        gender=labels.get("gender"),
    )


def _raise_for_http_status(status_code: int) -> None:
    """
    Map any non-2xx response onto 9a's error hierarchy. Every failure mode
    ElevenLabs documents (auth, quota, rate limit, outage) is a 4xx/5xx, so a
    single range check covers the whole table without special-casing each
    code individually.
    """

    if not (200 <= status_code < 300):
        raise SpeechProviderUnavailable(
            f"ElevenLabs request failed with status {status_code}",
        )


# Realtime STT message_type values that mean the connection/account itself is
# the problem - the same class of failure as an HTTP 401/403/429.
_REALTIME_UNAVAILABLE_MESSAGE_TYPES = frozenset(
    {"auth_error", "quota_exceeded", "rate_limited"},
)

# Values ElevenLabs documents for a mid-session transcription failure that is
# not a connection/account problem.
_REALTIME_ERROR_MESSAGE_TYPES = frozenset({"error", "transcriber_error"})


def _map_realtime_message(message: dict[str, Any]) -> TranscriptEvent | None:
    """
    Turn one decoded ElevenLabs realtime STT message into a TranscriptEvent,
    or raise the domain error it represents.

    An unrecognized message_type - including one ElevenLabs adds after this
    was written, and messages this feature does not otherwise care about such
    as session_started - is ignored (returns None) rather than raising: a new
    vendor message type must never drop a live call. A message missing
    message_type entirely is treated the same way.
    """

    message_type = message.get("message_type")

    if message_type == "partial_transcript":
        return TranscriptEvent(text=message.get("text", ""), is_final=False)

    if message_type == "committed_transcript":
        return TranscriptEvent(text=message.get("text", ""), is_final=True)

    if message_type in _REALTIME_UNAVAILABLE_MESSAGE_TYPES:
        raise SpeechProviderUnavailable(
            f"ElevenLabs realtime STT error: {message.get('error', message_type)}",
        )

    if message_type in _REALTIME_ERROR_MESSAGE_TYPES:
        raise SpeechProviderError(
            f"ElevenLabs realtime STT error: {message.get('error', message_type)}",
        )

    return None


class ElevenLabsTTS:
    """
    Text-to-speech via ElevenLabs' streaming HTTP endpoint.

    Accepts an injected httpx.AsyncClient for testing (MockTransport); when
    none is given, a client is created and closed per call so the provider
    itself never needs explicit lifecycle management from its caller.
    """

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = _BASE_URL,
        model_id: str = _DEFAULT_MODEL_ID,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._base_url = base_url
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        if not text:
            return

        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None

        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/text-to-speech/{voice_id}/stream",
                params={"output_format": "pcm_16000"},
                json={
                    "text": text,
                    "model_id": self._model_id,
                    "voice_settings": {"speed": speed},
                },
                headers={"xi-api-key": self._api_key},
                timeout=self._timeout_seconds,
            ) as response:
                _raise_for_http_status(response.status_code)

                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.TimeoutException as exc:
            raise SpeechProviderTimeout(
                "ElevenLabs text-to-speech request timed out",
            ) from exc
        except httpx.TransportError as exc:
            raise SpeechProviderUnavailable(
                "ElevenLabs text-to-speech connection failed",
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def list_voices(self) -> Sequence[Voice]:
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None

        voices: list[Voice] = []
        next_page_token: str | None = None
        pages_fetched = 0

        try:
            while True:
                params: dict[str, str] = {"page_size": str(_VOICES_PAGE_SIZE)}

                if next_page_token is not None:
                    params["next_page_token"] = next_page_token

                try:
                    response = await client.get(
                        f"{self._base_url}/v2/voices",
                        params=params,
                        headers={"xi-api-key": self._api_key},
                        timeout=self._timeout_seconds,
                    )
                except httpx.TimeoutException as exc:
                    raise SpeechProviderTimeout(
                        "ElevenLabs list voices request timed out",
                    ) from exc
                except httpx.TransportError as exc:
                    raise SpeechProviderUnavailable(
                        "ElevenLabs list voices connection failed",
                    ) from exc

                _raise_for_http_status(response.status_code)

                body = response.json()
                pages_fetched += 1

                voices.extend(_map_voice(raw) for raw in body.get("voices", []))

                if not body.get("has_more"):
                    break

                next_page_token = body.get("next_page_token")

                if next_page_token is None:
                    break

                if pages_fetched >= _MAX_VOICE_PAGES:
                    logger.warning(
                        "ElevenLabs voice catalogue pagination truncated "
                        "after %d pages",
                        pages_fetched,
                    )
                    break
        finally:
            if owns_client:
                await client.aclose()

        return voices


def _build_realtime_url(
    base_url: str,
    *,
    language: str,
    keywords: Sequence[str],
) -> str:
    """
    keyterms are repeated query parameters (keyterms=a&keyterms=b), not a
    single delimited value - confirmed against ElevenLabs' realtime STT
    examples while writing this feature's spec, since the docs' schema alone
    does not say.
    """

    params: list[tuple[str, str]] = [
        ("audio_format", "pcm_16000"),
        ("language_code", language),
        ("commit_strategy", "vad"),
    ]
    params.extend(("keyterms", keyword) for keyword in keywords)

    return f"{base_url}/v1/speech-to-text/realtime?{urlencode(params)}"


def _input_audio_chunk_message(chunk: bytes, *, commit: bool) -> str:
    return json.dumps(
        {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(chunk).decode("ascii"),
            "commit": commit,
        },
    )


async def _send_audio_chunks(connection: Any, audio: AsyncIterator[bytes]) -> None:
    """
    Upload every audio chunk as it arrives, marking the final one
    commit=true. An async iterator has no length or peek, so a one-item
    lookahead holds each chunk back until the next `async for` iteration
    proves whether it was the last one.

    Confirmed live against the real API: the server only closes the
    connection itself once it has finished processing the final commit and
    sent its response. If there was any real audio, this function does not
    close the connection - the caller's `async with` on the connection
    closes it once the receive loop ends, whether that is the server's own
    close after the final transcript or the caller abandoning the stream
    early. Closing here ourselves would race that response and discard it,
    exactly as the pre-fix version of this function did.

    An empty audio stream is different: nothing was ever sent, so there is
    no response to wait for and no reason for the server to ever close on
    its own - closing here is what lets an empty stream terminate cleanly
    instead of hanging forever.
    """

    pending: bytes | None = None

    async for chunk in audio:
        if pending is not None:
            await connection.send(_input_audio_chunk_message(pending, commit=False))

        pending = chunk

    if pending is None:
        await connection.close()
        return

    await connection.send(_input_audio_chunk_message(pending, commit=True))


class ElevenLabsSTT:
    """
    Speech-to-text via ElevenLabs' realtime WebSocket API (Scribe v2
    Realtime). Sending audio and receiving transcripts happen at the same
    time on the wire, so this runs them concurrently - a send-everything-
    then-receive-everything implementation would defeat both the streaming
    contract and the latency budget.

    Accepts an injected connect callable for testing (a fake async context
    manager, no real socket); when none is given, websockets.connect is used.
    """

    def __init__(
        self,
        *,
        api_key: str,
        connect: Callable[..., Any] | None = None,
        base_url: str = _WS_BASE_URL,
        open_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._connect = connect if connect is not None else websockets.connect
        self._base_url = base_url
        self._open_timeout_seconds = open_timeout_seconds

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keywords: Sequence[str] = (),
    ) -> AsyncIterator[TranscriptEvent]:
        url = _build_realtime_url(self._base_url, language=language, keywords=keywords)

        try:
            connection_cm = self._connect(
                url,
                additional_headers={"xi-api-key": self._api_key},
                open_timeout=self._open_timeout_seconds,
            )

            async with connection_cm as connection:
                send_task = asyncio.create_task(
                    _send_audio_chunks(connection, audio),
                )

                try:
                    async for raw_message in connection:
                        event = _map_realtime_message(json.loads(raw_message))

                        if event is not None:
                            yield event
                finally:
                    send_task.cancel()

                    with contextlib.suppress(asyncio.CancelledError):
                        await send_task
        except TimeoutError as exc:
            raise SpeechProviderTimeout(
                "ElevenLabs realtime STT connection timed out",
            ) from exc
        except websockets.exceptions.WebSocketException as exc:
            raise SpeechProviderUnavailable(
                "ElevenLabs realtime STT connection failed",
            ) from exc
