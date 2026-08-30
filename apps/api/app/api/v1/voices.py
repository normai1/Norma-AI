from fastapi import APIRouter, HTTPException, status
from norma_shared.speech import SpeechProviderError, SpeechProviderTimeout

from app.api.deps import CurrentUser, TtsProvider
from app.schemas.voice import VoiceResponse

router = APIRouter(prefix="/voices", tags=["voices"])

_VOICE_CATALOGUE_TIMEOUT = HTTPException(
    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
    detail="The voice catalogue is taking too long to load. Try again in a moment.",
)

_VOICE_CATALOGUE_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="The voice catalogue is temporarily unavailable.",
)


@router.get("", response_model=list[VoiceResponse])
async def list_voices(_: CurrentUser, tts: TtsProvider) -> list[VoiceResponse]:
    """
    The configured TTS provider's voice catalogue. Not organization or
    workspace scoped - a provider's voice catalogue is shared, not
    tenant-owned data.
    """

    try:
        voices = await tts.list_voices()
    except SpeechProviderTimeout as exc:
        raise _VOICE_CATALOGUE_TIMEOUT from exc
    except SpeechProviderError as exc:
        raise _VOICE_CATALOGUE_UNAVAILABLE from exc

    return [VoiceResponse.model_validate(voice) for voice in voices]
