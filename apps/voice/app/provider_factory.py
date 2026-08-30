"""
Speech-to-text provider selection for apps/voice. Mirrors
apps/api/app/providers/factory.py's exact shape and reasoning, scoped to
just STT for now - TTS provider selection is item 20e's job.
"""

from norma_shared.elevenlabs_speech import ElevenLabsSTT
from norma_shared.mock_speech import MockSTT
from norma_shared.speech import SpeechToTextProvider

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
