import pytest
from norma_shared.mock_speech import MockSTT, MockTTS

from app.provider_factory import (
    MissingElevenLabsApiKeyError,
    UnknownSpeechProviderError,
    get_stt_provider,
    get_tts_provider,
)


def test_default_provider_is_mock() -> None:
    assert isinstance(get_stt_provider(), MockSTT)


def test_explicit_mock_provider() -> None:
    assert isinstance(get_stt_provider("mock"), MockSTT)


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownSpeechProviderError):
        get_stt_provider("not-a-real-provider")


def test_elevenlabs_without_api_key_raises() -> None:
    with pytest.raises(MissingElevenLabsApiKeyError):
        get_stt_provider("elevenlabs")


def test_default_tts_provider_is_mock() -> None:
    assert isinstance(get_tts_provider(), MockTTS)


def test_explicit_mock_tts_provider() -> None:
    assert isinstance(get_tts_provider("mock"), MockTTS)


def test_unknown_tts_provider_raises() -> None:
    with pytest.raises(UnknownSpeechProviderError):
        get_tts_provider("not-a-real-provider")


def test_tts_elevenlabs_without_api_key_raises() -> None:
    with pytest.raises(MissingElevenLabsApiKeyError):
        get_tts_provider("elevenlabs")
