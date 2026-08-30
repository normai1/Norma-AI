"""
Speech provider selection: resolves a configured provider name to an
instance. This lives in its own module rather than speech.py because it must
import the concrete implementations (mock_speech.py today, the ElevenLabs
adapters once feature 9b lands), and speech.py must not import back from
either - that would be a circular import.
"""

from app.core.config import settings
from app.providers.elevenlabs_speech import ElevenLabsSTT, ElevenLabsTTS
from app.providers.embedding import EmbeddingProvider
from app.providers.local_storage import LocalStorage
from app.providers.mock_embedding import MockEmbeddingProvider
from app.providers.mock_speech import MockSTT, MockTTS
from app.providers.mock_storage import MockStorage
from app.providers.openai_embedding import OpenAIEmbeddingProvider
from app.providers.s3_storage import S3Storage
from app.providers.speech import SpeechToTextProvider, TextToSpeechProvider
from app.providers.storage import StorageProvider

_VALID_PROVIDER_NAMES = "'mock', 'elevenlabs'"
_VALID_STORAGE_PROVIDER_NAMES = "'mock', 'local', 's3'"
_VALID_EMBEDDING_PROVIDER_NAMES = "'mock', 'openai'"


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


class UnknownStorageProviderError(ValueError):
    """
    A configured STORAGE_PROVIDER name has no known implementation.
    """


class MissingS3ConfigError(ValueError):
    """
    The "s3" storage provider was selected but AWS credentials/bucket are
    unset. Fails at construction, not on the first upload attempt - the same
    reasoning MissingElevenLabsApiKeyError already established.
    """


class UnknownEmbeddingProviderError(ValueError):
    """
    A configured EMBEDDING_PROVIDER name has no known implementation.
    """


class MissingOpenAiApiKeyError(ValueError):
    """
    The "openai" embedding provider was selected but OPENAI_API_KEY is
    unset. Fails at construction, not on the first embed() call - the same
    reasoning MissingElevenLabsApiKeyError already established.
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


def get_tts_provider_dependency() -> TextToSpeechProvider:
    """
    FastAPI dependency entry point for the configured TTS provider.

    Deliberately not just `Depends(get_tts_provider)`: that function's
    optional `name` parameter would resolve as a request query parameter,
    letting any caller pick which provider serves them with an undocumented
    `?name=` override. This wrapper takes no arguments, closing that off.
    """

    return get_tts_provider()


def get_storage_provider(name: str | None = None) -> StorageProvider:
    """
    Resolve a storage provider by name, defaulting to STORAGE_PROVIDER.
    """

    provider_name = name if name is not None else settings.storage_provider

    if provider_name == "mock":
        return MockStorage()

    if provider_name == "local":
        return LocalStorage(base_dir=settings.local_storage_dir)

    if provider_name == "s3":
        if not (
            settings.aws_s3_bucket
            and settings.aws_region
            and settings.aws_access_key_id
            and settings.aws_secret_access_key
        ):
            raise MissingS3ConfigError(
                "STORAGE_PROVIDER=s3 requires AWS_S3_BUCKET, AWS_REGION, "
                "AWS_ACCESS_KEY_ID, and AWS_SECRET_ACCESS_KEY to all be set.",
            )

        return S3Storage(
            bucket=settings.aws_s3_bucket,
            region=settings.aws_region,
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
        )

    raise UnknownStorageProviderError(
        f"Unknown STORAGE_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_STORAGE_PROVIDER_NAMES}.",
    )


def get_storage_provider_dependency() -> StorageProvider:
    """
    FastAPI dependency entry point for the configured storage provider. Takes
    no arguments for the same reason get_tts_provider_dependency does not.
    """

    return get_storage_provider()


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """
    Resolve an embedding provider by name, defaulting to EMBEDDING_PROVIDER.
    """

    provider_name = name if name is not None else settings.embedding_provider

    if provider_name == "mock":
        return MockEmbeddingProvider(dimension=settings.embedding_dimension)

    if provider_name == "openai":
        if not settings.openai_api_key:
            raise MissingOpenAiApiKeyError(
                "OPENAI_API_KEY is not set. The 'openai' embedding "
                "provider requires it.",
            )

        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        )

    raise UnknownEmbeddingProviderError(
        f"Unknown EMBEDDING_PROVIDER {provider_name!r}. Valid options: "
        f"{_VALID_EMBEDDING_PROVIDER_NAMES}.",
    )


def get_embedding_provider_dependency() -> EmbeddingProvider:
    """
    FastAPI dependency entry point for the configured embedding provider.
    Takes no arguments for the same reason get_tts_provider_dependency does
    not.
    """

    return get_embedding_provider()
