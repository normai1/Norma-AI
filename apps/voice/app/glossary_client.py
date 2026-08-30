"""
Fetches an assistant's glossary terms from apps/api's internal API (item
20b) for STT keyword biasing. Failing open to an empty list on any error -
losing glossary biasing is an acceptable degradation, unlike losing
transcription itself, which must never happen silently.
"""

import uuid

import httpx

from app import config


async def fetch_glossary_terms(
    assistant_id: uuid.UUID, *, client: httpx.AsyncClient | None = None
) -> list[str]:
    """
    The assistant's glossary term strings, or an empty list if the fetch
    fails for any reason (connection error, timeout, non-200 response,
    malformed body). Accepts an injected httpx.AsyncClient for testing
    (MockTransport); when none is given, a client is created and closed
    per call, matching norma_shared.elevenlabs_speech's own lifecycle-
    management precedent.
    """

    owned_client = client or httpx.AsyncClient()

    try:
        response = await owned_client.get(
            f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/glossary",
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            timeout=5.0,
        )

        if response.status_code != 200:
            return []

        terms = response.json().get("terms", [])

        return terms if isinstance(terms, list) else []
    except httpx.HTTPError:
        return []
    finally:
        if client is None:
            await owned_client.aclose()
