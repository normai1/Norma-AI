"""
Speech-to-text and text-to-speech provider selection for apps/voice.
Mirrors apps/api/app/providers/factory.py's exact shape and reasoning.
"""

from norma_shared.elevenlabs_speech import ElevenLabsSTT, ElevenLabsTTS
from norma_shared.mock_speech import MockSTT, MockTTS
from norma_shared.speech import SpeechToTextProvider, TextToSpeechProvider

from app import config

_VALID_PROVIDER_NAMES = "'mock', 'elevenlabs'"


class UnknownSpeechProviderError(ValueError):
    """
    A configured STT_PROVIDER name has no known implementation.
    """


class MissingElevenLabsApiKeyError(ValueError):
    """
    The "elevenlabs" provider was selected but ELEVENLABS_API_KEY is unset.
    Fails at construction, not on the first stream() call - the same
    reasoning apps/api's own factory already established.
    """


def get_stt_provider(name: str | None = None) -> SpeechToTextProvider:
    """
    Resolve a speech-to-text provider by name, defaulting to STT_PROVIDER.
    """

    provider_name = name if name is not None else config.STT_PROVIDER

    if provider_name == "mock":
        return MockSTT()

    if provider_name == "elevenlabs":
        if not config.ELEVENLABS_API_KEY:
            raise MissingElevenLabsApiKeyError(
                "ELEVENLABS_API_KEY is not set. The 'elevenlabs' speech "
                "provider requires it.",
            )

        return ElevenLabsSTT(api_key=config.ELEVENLABS_API_KEY)

    raise UnknownSpeechProviderError(
        f"Unknown STT_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_PROVIDER_NAMES}.",
    )


def get_tts_provider(name: str | None = None) -> TextToSpeechProvider:
    """
    Resolve a text-to-speech provider by name, defaulting to TTS_PROVIDER.
    """

    provider_name = name if name is not None else config.TTS_PROVIDER

    if provider_name == "mock":
        return MockTTS()

    if provider_name == "elevenlabs":
        if not config.ELEVENLABS_API_KEY:
            raise MissingElevenLabsApiKeyError(
                "ELEVENLABS_API_KEY is not set. The 'elevenlabs' speech "
                "provider requires it.",
            )

        return ElevenLabsTTS(api_key=config.ELEVENLABS_API_KEY)

    raise UnknownSpeechProviderError(
        f"Unknown TTS_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_PROVIDER_NAMES}.",
    )
