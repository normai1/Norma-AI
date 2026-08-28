import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.providers.elevenlabs_speech import (
    _MAX_VOICE_PAGES,
    ElevenLabsSTT,
    ElevenLabsTTS,
    _map_realtime_message,
)
from app.providers.speech import (
    SpeechProviderError,
    SpeechProviderTimeout,
    SpeechProviderUnavailable,
    TranscriptEvent,
    Voice,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _audio_chunks(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


class _FakeConnection:
    """
    Stands in for a websockets ClientConnection: records what is sent,
    yields a scripted sequence of server messages, and tracks whether it was
    closed - by us (audio exhausted) or by the caller abandoning the stream.
    """

    def __init__(self, server_messages: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._server_messages = list(server_messages or [])
        self._index = 0

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> "_FakeConnection":
        return self

    async def __anext__(self) -> str:
        # Yields control once per message so the concurrently-running audio
        # sender gets scheduled - without this, a fake with zero scripted
        # messages could finish before the sender ever runs.
        await asyncio.sleep(0)

        # Deliberately does NOT stop just because close() was called: a real
        # websocket close is a graceful handshake, not an abrupt cutoff -
        # messages already sent by the peer stay deliverable until the
        # scripted list itself is exhausted. Stopping on `closed` here would
        # let our own post-audio close() race the peer's still-arriving
        # responses and silently drop them - exactly the bug this comment
        # replaced.
        if self._index >= len(self._server_messages):
            raise StopAsyncIteration

        message = self._server_messages[self._index]
        self._index += 1

        return message

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        self.closed = True

        return False


async def test_synthesize_streams_the_response_body_unchanged() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00\x01\x02\x03")

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    chunks = [chunk async for chunk in tts.synthesize("hello", voice_id="v1")]

    assert b"".join(chunks) == b"\x00\x01\x02\x03"

    await client.aclose()


async def test_synthesize_sends_speed_and_pcm_output_format() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["body"] = json.loads(request.content)

        return httpx.Response(200, content=b"audio")

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    async for _ in tts.synthesize("hi", voice_id="v1", speed=1.5):
        pass

    assert captured["params"]["output_format"] == "pcm_16000"
    assert captured["body"]["voice_settings"]["speed"] == 1.5
    assert captured["body"]["text"] == "hi"

    await client.aclose()


async def test_synthesize_empty_text_makes_no_request() -> None:
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True

        return httpx.Response(200, content=b"audio")

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    chunks = [chunk async for chunk in tts.synthesize("", voice_id="v1")]

    assert chunks == []
    assert called is False

    await client.aclose()


@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_synthesize_maps_error_statuses_to_unavailable(
    status_code: int,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=b"error")

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    with pytest.raises(SpeechProviderUnavailable):
        async for _ in tts.synthesize("hi", voice_id="v1"):
            pass

    await client.aclose()


async def test_synthesize_maps_a_transport_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    with pytest.raises(SpeechProviderTimeout):
        async for _ in tts.synthesize("hi", voice_id="v1"):
            pass

    await client.aclose()


async def test_synthesize_closing_the_stream_early_does_not_raise() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"a" * 10_000)

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    generator = tts.synthesize("a longer phrase to synthesize", voice_id="v1")
    await generator.__anext__()
    await generator.aclose()

    await client.aclose()


async def test_list_voices_maps_id_and_name() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [{"voice_id": "v1", "name": "Alex", "labels": {}}],
                "has_more": False,
            },
        )

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    voices = await tts.list_voices()

    assert voices == [Voice(id="v1", name="Alex", language="en", gender=None)]

    await client.aclose()


async def test_list_voices_reads_gender_from_labels() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "v1",
                        "name": "Priya",
                        "labels": {"gender": "female"},
                    },
                ],
                "has_more": False,
            },
        )

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    voices = await tts.list_voices()

    assert voices[0].gender == "female"

    await client.aclose()


async def test_list_voices_language_falls_back_labels_verified_default() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"voice_id": "v1", "name": "A", "labels": {"language": "fr"}},
                    {
                        "voice_id": "v2",
                        "name": "B",
                        "labels": {},
                        "verified_languages": [{"locale": "hi-IN"}],
                    },
                    {"voice_id": "v3", "name": "C", "labels": {}},
                ],
                "has_more": False,
            },
        )

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    voices = await tts.list_voices()

    assert voices[0].language == "fr"
    assert voices[1].language == "hi-IN"
    assert voices[2].language == "en"

    await client.aclose()


async def test_list_voices_follows_pagination_across_two_pages() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("next_page_token")

        if token is None:
            return httpx.Response(
                200,
                json={
                    "voices": [{"voice_id": "v1", "name": "A", "labels": {}}],
                    "has_more": True,
                    "next_page_token": "page2",
                },
            )

        assert token == "page2"

        return httpx.Response(
            200,
            json={
                "voices": [{"voice_id": "v2", "name": "B", "labels": {}}],
                "has_more": False,
            },
        )

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    voices = await tts.list_voices()

    assert [voice.id for voice in voices] == ["v1", "v2"]

    await client.aclose()


async def test_list_voices_stops_at_the_page_cap_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1

        return httpx.Response(
            200,
            json={
                "voices": [{"voice_id": f"v{call_count}", "name": "A", "labels": {}}],
                "has_more": True,
                "next_page_token": f"token{call_count}",
            },
        )

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    with caplog.at_level("WARNING"):
        voices = await tts.list_voices()

    assert len(voices) == _MAX_VOICE_PAGES
    assert call_count == _MAX_VOICE_PAGES
    assert any("truncated" in record.message for record in caplog.records)

    await client.aclose()


async def test_list_voices_maps_error_status() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"error")

    client = _client(handler)
    tts = ElevenLabsTTS(api_key="key", client=client)

    with pytest.raises(SpeechProviderUnavailable):
        await tts.list_voices()

    await client.aclose()


def test_map_realtime_message_partial_transcript_is_not_final() -> None:
    event = _map_realtime_message(
        {"message_type": "partial_transcript", "text": "hello the"},
    )

    assert event is not None
    assert event.text == "hello the"
    assert event.is_final is False
    assert event.confidence is None


def test_map_realtime_message_committed_transcript_is_final() -> None:
    event = _map_realtime_message(
        {"message_type": "committed_transcript", "text": "hello there"},
    )

    assert event is not None
    assert event.text == "hello there"
    assert event.is_final is True
    assert event.confidence is None


def test_map_realtime_message_session_started_is_ignored() -> None:
    assert _map_realtime_message({"message_type": "session_started"}) is None


@pytest.mark.parametrize(
    "message_type",
    ["auth_error", "quota_exceeded", "rate_limited"],
)
def test_map_realtime_message_account_errors_raise_unavailable(
    message_type: str,
) -> None:
    with pytest.raises(SpeechProviderUnavailable):
        _map_realtime_message({"message_type": message_type, "error": "nope"})


def test_map_realtime_message_transcriber_error_raises_base_error() -> None:
    with pytest.raises(SpeechProviderError):
        _map_realtime_message(
            {"message_type": "transcriber_error", "error": "boom"},
        )


def test_map_realtime_message_unrecognized_type_is_ignored() -> None:
    assert _map_realtime_message({"message_type": "some_future_message"}) is None


def test_map_realtime_message_missing_message_type_is_ignored() -> None:
    assert _map_realtime_message({"text": "no type here"}) is None


async def test_stt_stream_yields_ordered_events_from_scripted_messages() -> None:
    server_messages = [
        json.dumps({"message_type": "partial_transcript", "text": "hello"}),
        json.dumps({"message_type": "committed_transcript", "text": "hello there"}),
    ]
    connection = _FakeConnection(server_messages)
    stt = ElevenLabsSTT(api_key="key", connect=lambda url, **kwargs: connection)

    events = [
        event async for event in stt.stream(_audio_chunks([b"audio"]), language="en")
    ]

    assert events == [
        TranscriptEvent(text="hello", is_final=False),
        TranscriptEvent(text="hello there", is_final=True),
    ]


async def test_stt_stream_sends_audio_as_base64_input_audio_chunks() -> None:
    server_messages = [
        json.dumps({"message_type": "committed_transcript", "text": "done"}),
    ]
    connection = _FakeConnection(server_messages)
    stt = ElevenLabsSTT(api_key="key", connect=lambda url, **kwargs: connection)

    async for _ in stt.stream(_audio_chunks([b"hello-audio"]), language="en"):
        pass

    assert len(connection.sent) == 1
    sent = json.loads(connection.sent[0])
    assert sent["message_type"] == "input_audio_chunk"
    assert base64.b64decode(sent["audio_base_64"]) == b"hello-audio"
    assert sent["commit"] is False


async def test_stt_stream_sends_keywords_as_repeated_keyterms() -> None:
    connection = _FakeConnection([])
    captured_urls: list[str] = []

    def fake_connect(url: str, **kwargs: object) -> _FakeConnection:
        captured_urls.append(url)
        return connection

    stt = ElevenLabsSTT(api_key="key", connect=fake_connect)

    async for _ in stt.stream(
        _audio_chunks([]),
        language="en",
        keywords=["acme", "widget"],
    ):
        pass

    assert captured_urls[0].count("keyterms=acme") == 1
    assert captured_urls[0].count("keyterms=widget") == 1


async def test_stt_stream_empty_audio_terminates_cleanly() -> None:
    connection = _FakeConnection([])
    stt = ElevenLabsSTT(api_key="key", connect=lambda url, **kwargs: connection)

    events = [
        event async for event in stt.stream(_audio_chunks([]), language="en")
    ]

    assert events == []
    assert connection.closed is True


async def test_stt_stream_abandoning_the_iterator_closes_the_connection() -> None:
    server_messages = [
        json.dumps({"message_type": "partial_transcript", "text": "one"}),
        json.dumps({"message_type": "partial_transcript", "text": "two"}),
        json.dumps({"message_type": "partial_transcript", "text": "three"}),
    ]
    connection = _FakeConnection(server_messages)
    stt = ElevenLabsSTT(api_key="key", connect=lambda url, **kwargs: connection)

    generator = stt.stream(_audio_chunks([b"audio"]), language="en")
    await generator.__anext__()
    await generator.aclose()

    assert connection.closed is True


async def test_stt_stream_server_error_message_raises_mapped_error() -> None:
    server_messages = [json.dumps({"message_type": "auth_error", "error": "bad key"})]
    connection = _FakeConnection(server_messages)
    stt = ElevenLabsSTT(api_key="key", connect=lambda url, **kwargs: connection)

    with pytest.raises(SpeechProviderUnavailable):
        async for _ in stt.stream(_audio_chunks([b"audio"]), language="en"):
            pass
