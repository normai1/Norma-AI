"""
Speech provider selection: resolves a configured provider name to an
instance. This lives in its own module rather than speech.py because it must
import the concrete implementations (mock_speech.py today, the ElevenLabs
adapters once feature 9b lands), and speech.py must not import back from
either - that would be a circular import.
"""

from app.core.config import settings
from app.providers.elevenlabs_speech import ElevenLabsSTT, ElevenLabsTTS
from app.providers.mock_speech import MockSTT, MockTTS
from app.providers.speech import SpeechToTextProvider, TextToSpeechProvider

_VALID_PROVIDER_NAMES = "'mock', 'elevenlabs'"


class UnknownSpeechProviderError(ValueError):
    """
    A configured STT_PROVIDER or TTS_PROVIDER name has no known
    implementation. A misconfiguration, not a runtime provider failure -
    deliberately not part of the SpeechProviderError hierarchy.
    """


class MissingElevenLabsApiKeyError(ValueError):
    """
    The "elevenlabs" provider was selected but ELEVENLABS_API_KEY is unset. A
    misconfigured deploy must fail here, at construction, rather than
    discovering the missing key mid-call (CLAUDE.md section 9).
    """


def _require_elevenlabs_api_key() -> str:
    if not settings.elevenlabs_api_key:
        raise MissingElevenLabsApiKeyError(
            "ELEVENLABS_API_KEY is not set. The 'elevenlabs' speech "
            "provider requires it.",
        )

    return settings.elevenlabs_api_key


def get_stt_provider(name: str | None = None) -> SpeechToTextProvider:
    """
    Resolve a speech-to-text provider by name, defaulting to STT_PROVIDER.
    """

    provider_name = name if name is not None else settings.stt_provider

    if provider_name == "mock":
        return MockSTT()

    if provider_name == "elevenlabs":
        return ElevenLabsSTT(api_key=_require_elevenlabs_api_key())

    raise UnknownSpeechProviderError(
        f"Unknown STT_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_PROVIDER_NAMES}.",
    )


def get_tts_provider(name: str | None = None) -> TextToSpeechProvider:
    """
    Resolve a text-to-speech provider by name, defaulting to TTS_PROVIDER.
    """

    provider_name = name if name is not None else settings.tts_provider

    if provider_name == "mock":
        return MockTTS()

    if provider_name == "elevenlabs":
        return ElevenLabsTTS(api_key=_require_elevenlabs_api_key())

    raise UnknownSpeechProviderError(
        f"Unknown TTS_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_PROVIDER_NAMES}.",
    )
