from collections.abc import AsyncIterator

import pytest

from app.core.config import settings
from app.providers.elevenlabs_speech import ElevenLabsSTT, ElevenLabsTTS
from app.providers.factory import (
    MissingElevenLabsApiKeyError,
    UnknownSpeechProviderError,
    get_stt_provider,
    get_tts_provider,
)
from app.providers.mock_speech import MockSTT, MockTTS
from app.providers.speech import SpeechProviderError, TranscriptEvent, Voice


async def _audio_chunks(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def test_stt_yields_scripted_events_in_order() -> None:
    script = [
        TranscriptEvent(text="hello", is_final=False),
        TranscriptEvent(text="hello there", is_final=True, confidence=0.97),
    ]
    stt = MockSTT(script=script)

    events = [
        event async for event in stt.stream(_audio_chunks([b"audio"]), language="en")
    ]

    assert events == script


async def test_stt_handles_empty_audio_stream() -> None:
    script = [TranscriptEvent(text="hi", is_final=True)]
    stt = MockSTT(script=script)

    events = [event async for event in stt.stream(_audio_chunks([]), language="en")]

    assert events == script


async def test_stt_closing_the_stream_early_does_not_raise() -> None:
    script = [
        TranscriptEvent(text="one", is_final=False),
        TranscriptEvent(text="two", is_final=False),
        TranscriptEvent(text="three", is_final=True),
    ]
    stt = MockSTT(script=script)

    generator = stt.stream(_audio_chunks([b"audio"]), language="en")
    seen = []

    async for event in generator:
        seen.append(event)

        if len(seen) == 1:
            break

    await generator.aclose()

    assert seen == script[:1]


async def test_stt_injected_failure_surfaces() -> None:
    failure = SpeechProviderError("provider disconnected")
    stt = MockSTT(
        script=[TranscriptEvent(text="partial", is_final=False)],
        failure=failure,
    )

    events: list[TranscriptEvent] = []

    with pytest.raises(SpeechProviderError):
        async for event in stt.stream(_audio_chunks([b"audio"]), language="en"):
            events.append(event)

    assert events == [TranscriptEvent(text="partial", is_final=False)]


async def test_stt_applies_a_per_event_delay() -> None:
    stt = MockSTT(
        script=[TranscriptEvent(text="hi", is_final=True)],
        event_delay_seconds=0.01,
    )

    events = [
        event async for event in stt.stream(_audio_chunks([b"audio"]), language="en")
    ]

    assert len(events) == 1


async def _synthesize_all(tts: MockTTS, text: str) -> bytes:
    chunks = [
        chunk async for chunk in tts.synthesize(text, voice_id="v1")
    ]

    return b"".join(chunks)


async def test_tts_is_deterministic_for_the_same_text() -> None:
    tts = MockTTS()

    first = await _synthesize_all(tts, "hello there")
    second = await _synthesize_all(tts, "hello there")

    assert first == second


async def test_tts_output_length_is_proportional_to_text_length() -> None:
    tts = MockTTS(bytes_per_character=100)

    short = await _synthesize_all(tts, "hi")
    long = await _synthesize_all(tts, "hi" * 10)

    assert len(long) == len(short) * 10


async def test_tts_empty_text_yields_no_audio() -> None:
    tts = MockTTS()

    audio = await _synthesize_all(tts, "")

    assert audio == b""


async def test_tts_list_voices_returns_the_configured_catalogue() -> None:
    voices = [
        Voice(id="v1", name="Alex", language="en-US"),
        Voice(id="v2", name="Priya", language="hi-IN", gender="female"),
    ]
    tts = MockTTS(voices=voices)

    assert await tts.list_voices() == voices


async def test_tts_list_voices_raises_the_injected_failure() -> None:
    failure = SpeechProviderError("provider unavailable")
    voices = [Voice(id="v1", name="Alex", language="en-US")]
    tts = MockTTS(voices=voices, failure=failure)

    with pytest.raises(SpeechProviderError):
        await tts.list_voices()


async def test_tts_marks_cancelled_when_abandoned_mid_stream() -> None:
    tts = MockTTS(bytes_per_character=100, chunk_size_bytes=50)

    generator = tts.synthesize("a fairly long sentence to synthesize", voice_id="v1")
    await generator.__anext__()
    await generator.aclose()

    assert tts.cancelled is True


async def test_tts_injected_failure_surfaces() -> None:
    failure = SpeechProviderError("provider unavailable")
    tts = MockTTS(failure=failure)

    with pytest.raises(SpeechProviderError):
        await _synthesize_all(tts, "hello")


def test_factory_resolves_mock_stt_by_name() -> None:
    assert isinstance(get_stt_provider("mock"), MockSTT)


def test_factory_resolves_mock_tts_by_name() -> None:
    assert isinstance(get_tts_provider("mock"), MockTTS)


def test_factory_rejects_an_unknown_stt_provider_name() -> None:
    with pytest.raises(UnknownSpeechProviderError):
        get_stt_provider("not-a-real-provider")


def test_factory_rejects_an_unknown_tts_provider_name() -> None:
    with pytest.raises(UnknownSpeechProviderError):
        get_tts_provider("not-a-real-provider")


def test_factory_falls_back_to_configured_settings_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "stt_provider", "mock")
    monkeypatch.setattr(settings, "tts_provider", "mock")

    assert isinstance(get_stt_provider(), MockSTT)
    assert isinstance(get_tts_provider(), MockTTS)


def test_factory_resolves_elevenlabs_stt_when_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "a-real-key")

    assert isinstance(get_stt_provider("elevenlabs"), ElevenLabsSTT)


def test_factory_resolves_elevenlabs_tts_when_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "a-real-key")

    assert isinstance(get_tts_provider("elevenlabs"), ElevenLabsTTS)


def test_factory_rejects_elevenlabs_stt_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")

    with pytest.raises(MissingElevenLabsApiKeyError):
        get_stt_provider("elevenlabs")


def test_factory_rejects_elevenlabs_tts_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")

    with pytest.raises(MissingElevenLabsApiKeyError):
        get_tts_provider("elevenlabs")
