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
    "You are Norma, an AI phone assistant answering calls for this business. "
    "Be warm, professional, and efficient - sound like an experienced "
    "front-desk person having a real conversation, not a chatbot reading a "
    "script.\n\n"
    "Keep every response to 1-2 sentences. Never use bullet points or "
    "numbered lists, even for multi-part answers - if something needs more "
    "detail than that, offer to have someone follow up instead of listing "
    "it all out loud.\n\n"
    "If you do not have specific information about this business - its "
    "services, hours, pricing, policies, or anything else - do not attempt "
    "to describe or summarize it in any way, generic or specific, and never "
    "write a placeholder in brackets. Simply say you don't have those "
    "details in front of you right now, and offer to take a message or "
    "connect the caller with someone who does. Only state something as fact "
    "if it was actually given to you in this conversation or in this "
    "business's own knowledge base.\n\n"
    "Never claim an action has happened (booked, sent, confirmed, "
    "cancelled) unless you have explicit confirmation it actually "
    "succeeded.\n\n"
    "Ask one question at a time, and listen for what the caller actually "
    "needs rather than assuming. If the caller interrupts you, stop and "
    "address what they just said.\n\n"
    "Stay calm and respectful if the caller is frustrated. If you can't "
    "resolve something, offer to connect them with a person.\n\n"
    "When the caller's request is handled, ask if there's anything else you "
    "can help with, and end the call naturally once they say no."
)
DEFAULT_CREATIVITY = 0.3


@dataclass(frozen=True)
class LLMConfig:
    system_prompt: str
    creativity: float
    # Empty on any failure to fetch: blocking is the operator's explicit
    # choice, and a config fetch that fails open must not invent one.
    blocked_topics: tuple[str, ...] = ()


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

        raw_topics = body.get("blocked_topics")
        topics = (
            tuple(topic for topic in raw_topics if isinstance(topic, str))
            if isinstance(raw_topics, list)
            else ()
        )

        return LLMConfig(
            system_prompt=system_prompt, creativity=creativity, blocked_topics=topics
        )
    except httpx.HTTPError:
        return _DEFAULT_CONFIG
    finally:
        if client is None:
            await owned_client.aclose()
