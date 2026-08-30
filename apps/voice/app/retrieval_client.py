"""
Fetches retrieved knowledge context for one turn's query from apps/api's
internal API (item 20d). Fails open to "" on any error - an empty context
is CLAUDE.md section 39's "empty retrieval results" case, a normal, handled
outcome for the LLM turn loop, not just an error fallback.
"""

import uuid

import httpx

from app import config


async def fetch_retrieved_context(
    assistant_id: uuid.UUID, query: str, *, client: httpx.AsyncClient | None = None
) -> str:
    owned_client = client or httpx.AsyncClient()

    try:
        response = await owned_client.post(
            f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/retrieve",
            json={"query": query},
            headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
            timeout=5.0,
        )

        if response.status_code != 200:
            return ""

        context = response.json().get("context")

        return context if isinstance(context, str) else ""
    except httpx.HTTPError:
        return ""
    finally:
        if client is None:
            await owned_client.aclose()
