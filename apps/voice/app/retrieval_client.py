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
import os
import time
import uuid

import httpx

from app import config
from app.internal_api import internal_headers

logger = logging.getLogger(__name__)

# Lowered from 5s once the logging above showed what that cost. A retrieval
# that took the full five seconds did not merely answer late: the caller heard
# nothing, concluded the assistant had not understood, and spoke again - and
# that second utterance barged in and cancelled their own pending turn. The
# turn metrics recorded it as stt finalized, no retrieval, no LLM token, no
# audio: seven of thirty-five turns, reported as "sometimes it is not
# responding anything".
#
# So the timeout is a budget for how long a caller will sit in silence, not
# for how long the provider might take. CLAUDE.md allows retrieval 80ms; the
# hosted embedding provider measures 0.4s warm and over 5s cold, so this
# cannot be met today and the honest choice is to answer without knowledge
# rather than to keep waiting. The assistant then says it does not have the
# detail, which is a worse answer than the one knowledge would have given and
# a far better one than silence.
#
# Configurable because the right value follows the embedding provider: hosting
# the model locally, or caching query embeddings, would make a tighter budget
# affordable and a looser one unnecessary.
_TIMEOUT_SECONDS = float(os.environ.get("RETRIEVAL_TIMEOUT_SECONDS", "1.5"))

# The longest a caller will sit in silence for retrieval, across every
# attempt. One attempt that fails and one that succeeds has to fit inside
# this, or the retry trades a missing answer for the failure the single
# timeout was lowered to avoid: a caller who assumes they were not heard,
# speaks again, and barges in on their own pending turn.
_TOTAL_BUDGET_SECONDS = float(os.environ.get("RETRIEVAL_TOTAL_BUDGET_SECONDS", "3.5"))

# Below this there is not enough left for a retry to plausibly finish, and
# starting one only adds silence before the same answer.
_MIN_RETRY_SECONDS = 0.75


class RetrievedContext(str):
    """
    This turn's knowledge, and - when there is none - why there is none.

    A `str` subclass so nothing that already consumes this has to change: the
    value *is* the context text, and every existing caller and test double
    keeps working unchanged.

    The flag answers the one question the text cannot. An empty string means
    two opposite things: the knowledge base was searched and does not cover
    this, or it was never searched because the lookup did not finish. The
    first justifies "I don't have that detail"; the second makes that
    sentence a falsehood - the assistant may well have the detail and simply
    failed to look. Callers that cannot tell them apart have to guess, and
    the guess was reported as the assistant refusing a question it answers
    perfectly well a moment later.
    """

    lookup_failed: bool

    def __new__(cls, text: str = "", *, lookup_failed: bool = False) -> "RetrievedContext":
        value = super().__new__(cls, text)
        value.lookup_failed = lookup_failed

        return value


async def fetch_retrieved_context(
    assistant_id: uuid.UUID, query: str, *, client: httpx.AsyncClient | None = None
) -> RetrievedContext:
    owned_client = client or httpx.AsyncClient()
    started = time.monotonic()

    try:
        try:
            response = await owned_client.post(
                f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}/retrieve",
                json={"query": query},
                headers=internal_headers(),
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            # The likeliest real cause today: the configured embedding
            # provider is a hosted API, not a local model, and a cold or
            # queued call can take several seconds - see
            # HuggingFaceEmbeddingProvider's own, much longer timeout.
            #
            # Its slowness is erratic rather than steady, which is what makes
            # a second attempt worth making instead of giving up. Measured on
            # one call, the same knowledge answered a question in 4148ms and
            # then in 190ms - so the caller was told the assistant could not
            # find it, asked again, and got the answer. Reported as "it
            # should answer on the first attempt", which is the right ask:
            # from a caller's side, asking once should be enough.
            #
            # Bounded by a total, not by a count, because what a caller
            # actually notices is how long they sit in silence. The first
            # attempt has already spent its budget; the retry gets whatever
            # is left of the total and no more.
            remaining = _TOTAL_BUDGET_SECONDS - (time.monotonic() - started)

            if remaining < _MIN_RETRY_SECONDS:
                logger.warning(
                    "retrieval timed out after %.1fs: assistant=%s",
                    time.monotonic() - started,
                    assistant_id,
                )

                return RetrievedContext(lookup_failed=True)

            logger.info(
                "retrieval timed out after %.1fs - retrying with %.1fs left: "
                "assistant=%s",
                time.monotonic() - started,
                remaining,
                assistant_id,
            )

            try:
                response = await owned_client.post(
                    f"{config.API_INTERNAL_URL}/internal/v1/assistants/"
                    f"{assistant_id}/retrieve",
                    json={"query": query},
                    headers=internal_headers(),
                    timeout=remaining,
                )
            except (httpx.TimeoutException, httpx.HTTPError):
                logger.warning(
                    "retrieval timed out twice after %.1fs: assistant=%s",
                    time.monotonic() - started,
                    assistant_id,
                )

                return RetrievedContext(lookup_failed=True)
        except httpx.HTTPError as exc:
            logger.warning(
                "retrieval request failed: assistant=%s error=%s",
                assistant_id,
                type(exc).__name__,
            )
            return RetrievedContext(lookup_failed=True)

        if response.status_code != 200:
            logger.warning(
                "retrieval returned status %d: assistant=%s",
                response.status_code,
                assistant_id,
            )
            return RetrievedContext(lookup_failed=True)

        try:
            body = response.json()
            context = body.get("context")
        except ValueError:
            # json.JSONDecodeError is a ValueError - not an httpx.HTTPError,
            # so it needs its own catch to keep this function's "never
            # raises" contract on a malformed (but 200) response body.
            logger.warning(
                "retrieval response was not valid JSON: assistant=%s", assistant_id
            )
            return RetrievedContext(lookup_failed=True)

        if not isinstance(context, str):
            logger.warning(
                "retrieval response had no usable context field: assistant=%s",
                assistant_id,
            )
            return RetrievedContext(lookup_failed=True)

        _log_what_was_retrieved(assistant_id, body)

        return RetrievedContext(context)
    finally:
        if client is None:
            await owned_client.aclose()


# Generous, because nothing is waiting on it: this runs once at session
# start, alongside the other config fetches, while the greeting plays. The
# only thing a timeout here costs is that the first few turns embed their
# own questions the slow way, exactly as they did before warming existed.
_WARM_TIMEOUT_SECONDS = 20.0


async def warm_retrieval_cache(
    assistant_id: uuid.UUID, *, client: httpx.AsyncClient | None = None
) -> None:
    """
    Ask the API to pre-embed this assistant's FAQ questions before the
    conversation starts.

    The hosted embedding provider is bimodal - measured at 0.28-0.43s most
    of the time, with roughly one call in three taking 4-12s - and the
    per-turn timeout above will not wait for the slow tail. Warming moves
    that unpredictability out of the turn: the questions callers actually
    ask are already embedded, so retrieval is a database lookup.

    Never raises. A failure here degrades answer quality slightly and is
    logged; it must not be able to stop a session from starting.
    """

    owned_client = client or httpx.AsyncClient()

    try:
        response = await owned_client.post(
            f"{config.API_INTERNAL_URL}/internal/v1/assistants/{assistant_id}"
            "/retrieve/warm",
            headers=internal_headers(),
            timeout=_WARM_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            logger.warning(
                "retrieval cache warm returned status %d: assistant=%s",
                response.status_code,
                assistant_id,
            )
            return

        logger.info(
            "retrieval cache warmed: assistant=%s entries=%s",
            assistant_id,
            response.json().get("warmed"),
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "retrieval cache warm failed: assistant=%s error=%s",
            assistant_id,
            type(exc).__name__,
        )
    finally:
        if client is None:
            await owned_client.aclose()


def _log_what_was_retrieved(assistant_id: uuid.UUID, body: object) -> None:
    """
    Record what knowledge this turn was actually given.

    Scores and source identifiers only - never chunk text, never the
    caller's words (CLAUDE.md section 27). The API logs the same decision on
    its own side; this is the copy that sits in the call's own log next to
    the turn it belongs to, which is where anyone asking "why did it answer
    that?" is already looking.

    Retrieval returns its top matches whatever their distance, so a question
    the knowledge does not cover still comes back with a full set of
    least-bad ones. The scores are what make that visible: a turn answered
    from chunks scoring 0.2 reads exactly like one answered from 0.9 until
    somebody prints the numbers.
    """

    if not isinstance(body, dict):
        return

    retrieved = body.get("retrieved")

    if not isinstance(retrieved, list) or not retrieved:
        logger.info("retrieved nothing: assistant=%s", assistant_id)

        return

    scores = [item.get("score") for item in retrieved if isinstance(item, dict)]
    used = sum(1 for item in retrieved if isinstance(item, dict) and item.get("used"))

    logger.info(
        "retrieved %d chunks (%d reached the model): assistant=%s scores=%s",
        len(retrieved),
        used,
        assistant_id,
        scores,
    )
