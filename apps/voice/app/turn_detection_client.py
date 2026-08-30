"""
Fetches an assistant's turn-detection sensitivity from apps/api's internal
API (item 20c). Fails open to the schema's own default (0.5) on any error -
mirrors app/glossary_client.py's exact shape and reasoning: losing a
sensitivity override is an acceptable degradation, unlike losing
transcription or turn detection itself, which must never happen silently.
"""

import uuid

import httpx

from app import config

# app/schemas/assistant_version.py's own turn_sensitivity default - what an
# assistant would have if it had never been published, which is also the
# safest fallback when the internal API can't be reached at all.
DEFAULT_SENSITIVITY = 0.5


async def fetch_turn_sensitivity(
    assistant_id: uuid.UUID, *, client: httpx.AsyncClient | None = None
) -> float:
    """
    The assistant's turn_sensitivity, or DEFAULT_SENSITIVITY if the fetch
    fails for any reason (connection error, timeout, non-200 response,
    malformed body). Accepts an injected httpx.AsyncClient for testing
    (MockTransport); when none is given, a client is created and closed per
    call, matching fetch_glossary_terms's own lifecycle-management
    precedent.
    """

    owned_client = client or httpx.AsyncClient()

    try:
        response = await owned_client.get(
            f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/turn-detection-config",
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            timeout=5.0,
        )

        if response.status_code != 200:
            return DEFAULT_SENSITIVITY

        sensitivity = response.json().get("sensitivity")

        return sensitivity if isinstance(sensitivity, (int, float)) else DEFAULT_SENSITIVITY
    except httpx.HTTPError:
        return DEFAULT_SENSITIVITY
    finally:
        if client is None:
            await owned_client.aclose()
