"""
Fetches an assistant's voice_id and speech_rate from apps/api's internal
API (item 20e) once at session setup. Fails open to a fixed default on any
error - mirrors app/llm_config_client.py's exact shape.
"""

import uuid
from dataclasses import dataclass

import httpx

from app import config

# Mirrors apps/api/app/services/tts_config.py's own fixed defaults exactly.
DEFAULT_VOICE_ID = "default"
DEFAULT_SPEECH_RATE = 1.0


@dataclass(frozen=True)
class TTSConfig:
    voice_id: str
    speech_rate: float


_DEFAULT_CONFIG = TTSConfig(voice_id=DEFAULT_VOICE_ID, speech_rate=DEFAULT_SPEECH_RATE)


async def fetch_tts_config(
    assistant_id: uuid.UUID, *, client: httpx.AsyncClient | None = None
) -> TTSConfig:
    """
    The assistant's voice_id and speech_rate, or the fixed defaults if the
    fetch fails for any reason (connection error, timeout, non-200
    response, malformed body).
    """

    owned_client = client or httpx.AsyncClient()

    try:
        response = await owned_client.get(
            f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/tts-config",
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            timeout=5.0,
        )

        if response.status_code != 200:
            return _DEFAULT_CONFIG

        body = response.json()
        voice_id = body.get("voice_id")
        speech_rate = body.get("speech_rate")

        if not isinstance(voice_id, str) or not isinstance(speech_rate, (int, float)):
            return _DEFAULT_CONFIG

        return TTSConfig(voice_id=voice_id, speech_rate=speech_rate)
    except httpx.HTTPError:
        return _DEFAULT_CONFIG
    finally:
        if client is None:
            await owned_client.aclose()
