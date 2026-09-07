"""
Fetches retrieved knowledge context for one turn's query from apps/api's
internal API (item 20d). Fails open to "" on any error - an empty context
is CLAUDE.md section 39's "empty retrieval results" case, a normal, handled
outcome for the LLM turn loop, not just an error fallback.

CLAUDE.md section 27 lists "retrieval failures and empty results" among the
signals that must be observable - unlike this module's per-session siblings
(glossary_client.py and friends), retrieval runs on every turn, so a failure
here silently drops real, already-embedded knowledge from that turn's answer.
Without the logging below, that was indistinguishable from the document
genuinely not covering the question - reported live as "the assistant only
answers the auto-generated FAQs, nothing else in the document", traced to
retrieval timing out under the real embedding provider's latency rather than
any gap in what was actually indexed.
"""

import logging
import time
import uuid

import httpx

from app import config

logger = logging.getLogger(__name__)

# Deliberately not raised to "safely" cover a slow embedding call: this fetch
# runs before config.LLM_FIRST_TOKEN_TIMEOUT_SECONDS's own clock even starts,
# so a longer timeout here would make the caller wait even longer on the turns
# that are already struggling, trading one silent failure for a slower one.
# Logging what actually happened is what makes the trade-off visible instead
# of guessing at a bigger number.
_TIMEOUT_SECONDS = 5.0


async def fetch_retrieved_context(
    assistant_id: uuid.UUID, query: str, *, client: httpx.AsyncClient | None = None
) -> str:
    owned_client = client or httpx.AsyncClient()
    started = time.monotonic()

    try:
        try:
            response = await owned_client.post(
                f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/retrieve",
                json={"query": query},
                headers={"X-Internal-Secret": config.INTERNAL_API_SECRET},
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            # The likeliest real cause today: the configured embedding
            # provider is a hosted API, not a local model, and a cold or
            # queued call can take several seconds - see
            # HuggingFaceEmbeddingProvider's own, much longer timeout.
            logger.warning(
                "retrieval timed out after %.1fs: assistant=%s",
                time.monotonic() - started,
                assistant_id,
            )
            return ""
        except httpx.HTTPError as exc:
            logger.warning(
                "retrieval request failed: assistant=%s error=%s",
                assistant_id,
                type(exc).__name__,
            )
            return ""

        if response.status_code != 200:
            logger.warning(
                "retrieval returned status %d: assistant=%s",
                response.status_code,
                assistant_id,
            )
            return ""

        try:
            context = response.json().get("context")
        except ValueError:
            # json.JSONDecodeError is a ValueError - not an httpx.HTTPError,
            # so it needs its own catch to keep this function's "never
            # raises" contract on a malformed (but 200) response body.
            logger.warning(
                "retrieval response was not valid JSON: assistant=%s", assistant_id
            )
            return ""

        if not isinstance(context, str):
            logger.warning(
                "retrieval response had no usable context field: assistant=%s",
                assistant_id,
            )
            return ""

        return context
    finally:
        if client is None:
            await owned_client.aclose()
