"""
Fetches an assistant's system prompt and creativity from apps/api's internal
API (item 20d) once at session setup. Fails open to a fixed default on any
error - mirrors app/glossary_client.py's and app/turn_detection_client.py's
exact shape: losing this configuration is an acceptable degradation, unlike
losing transcription or turn detection, which must never happen silently.
"""

import uuid
from dataclasses import dataclass

import httpx

from app import config

# Mirrors apps/api/app/services/llm_config.py's own fixed defaults exactly -
# what a live session falls back to if the internal API can't be reached at
# all, same as an unpublished assistant would get.
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful AI phone assistant. Answer briefly and clearly, "
    "and only state information you actually know."
)
DEFAULT_CREATIVITY = 0.3


@dataclass(frozen=True)
class LLMConfig:
    system_prompt: str
    creativity: float


_DEFAULT_CONFIG = LLMConfig(system_prompt=DEFAULT_SYSTEM_PROMPT, creativity=DEFAULT_CREATIVITY)


async def fetch_llm_config(
    assistant_id: uuid.UUID, *, client: httpx.AsyncClient | None = None
) -> LLMConfig:
    """
    The assistant's system prompt and creativity, or the fixed defaults if
    the fetch fails for any reason (connection error, timeout, non-200
    response, malformed body).
    """

    owned_client = client or httpx.AsyncClient()

    try:
        response = await owned_client.get(
            f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/llm-config",
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            timeout=5.0,
        )

        if response.status_code != 200:
            return _DEFAULT_CONFIG

        body = response.json()
        system_prompt = body.get("system_prompt")
        creativity = body.get("creativity")

        if not isinstance(system_prompt, str) or not isinstance(creativity, (int, float)):
            return _DEFAULT_CONFIG

        return LLMConfig(system_prompt=system_prompt, creativity=creativity)
    except httpx.HTTPError:
        return _DEFAULT_CONFIG
    finally:
        if client is None:
            await owned_client.aclose()
