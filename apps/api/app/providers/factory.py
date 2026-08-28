"""
Speech provider selection: resolves a configured provider name to an
instance. This lives in its own module rather than speech.py because it must
import the concrete implementations (mock_speech.py today, the ElevenLabs
adapters once feature 9b lands), and speech.py must not import back from
either - that would be a circular import.
"""

from app.core.config import settings
from app.providers.mock_speech import MockSTT, MockTTS
from app.providers.speech import SpeechToTextProvider, TextToSpeechProvider


class UnknownSpeechProviderError(ValueError):
    """
    A configured STT_PROVIDER or TTS_PROVIDER name has no known
    implementation. A misconfiguration, not a runtime provider failure -
    deliberately not part of the SpeechProviderError hierarchy.
    """


def get_stt_provider(name: str | None = None) -> SpeechToTextProvider:
    """
    Resolve a speech-to-text provider by name, defaulting to STT_PROVIDER.
    """

    provider_name = name if name is not None else settings.stt_provider

    if provider_name == "mock":
        return MockSTT()

    raise UnknownSpeechProviderError(
        f"Unknown STT_PROVIDER {provider_name!r}. Valid options: 'mock'.",
    )


def get_tts_provider(name: str | None = None) -> TextToSpeechProvider:
    """
    Resolve a text-to-speech provider by name, defaulting to TTS_PROVIDER.
    """

    provider_name = name if name is not None else settings.tts_provider

    if provider_name == "mock":
        return MockTTS()

    raise UnknownSpeechProviderError(
        f"Unknown TTS_PROVIDER {provider_name!r}. Valid options: 'mock'.",
    )
