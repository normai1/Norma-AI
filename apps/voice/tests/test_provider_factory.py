import pytest
from norma_shared.mock_speech import MockSTT, MockTTS

from app import config
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


def test_tts_elevenlabs_uses_the_configured_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Real per-turn cost, not a cosmetic default: eleven_multilingual_v2 (the
    shared adapter's own fallback) measured 1.1-2.1s time-to-first-byte in
    this environment, versus ~330-380ms for eleven_flash_v2_5 - most of a
    whole turn's latency budget spent on TTS alone. TTS_MODEL_ID must
    actually reach the constructed provider, not just exist as an unused
    setting.
    """

    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(config, "TTS_MODEL_ID", "eleven_flash_v2_5")

    provider = get_tts_provider("elevenlabs")

    assert provider._model_id == "eleven_flash_v2_5"
