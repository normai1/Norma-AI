"""
One kept-alive HTTP client for the hosted embedding providers, shared by
every request in the process.

Both hosted providers were written to create an httpx.AsyncClient per
embed() call and close it again - fine for the crawl and upload paths, which
embed in the background and do not care about a handshake, but this is also
the call that sits inside a live turn. Retrieval embeds the caller's question
before anything can be searched, so every turn paid a fresh DNS lookup, TCP
connect and TLS handshake to a remote host before the model was even asked.

Measured against router.huggingface.co from a development machine, one text,
BAAI/bge-base-en-v1.5:

    client created per call:   1.598s, 1.022s, 0.882s, 0.998s
    one kept-alive client:     0.333s, 0.306s, 0.302s, 0.284s, 0.283s

That is roughly 700ms per turn, and - the part that actually broke calls -
almost all of the variance with it. CLAUDE.md section 11 lists pre-warmed
connections first among the techniques that keep retrieval inside the
latency budget; this is that.

The client is created lazily on first use rather than at import, so a
process that never embeds never opens a pool, and closed from the
application lifespan.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

# Generous, and deliberately not the per-turn budget: the crawl and upload
# paths embed batches of dozens of chunks through this same client and a
# large batch legitimately takes tens of seconds. What protects a live turn
# is the media plane's own retrieval timeout (apps/voice's retrieval_client),
# which abandons the turn's retrieval and answers without knowledge rather
# than leaving the caller in silence.
_TIMEOUT_SECONDS = 30.0

# Small: this pool serves one host and the work is request-response, not
# streaming. Keeping connections alive is the entire point, so the idle
# expiry is long enough to survive the gaps between turns in a conversation.
_LIMITS = httpx.Limits(
    max_connections=10,
    max_keepalive_connections=10,
    keepalive_expiry=300.0,
)

# See warm_embedding_connection for why this is not 1.
_WARM_UP_CALLS = 3

_client: httpx.AsyncClient | None = None


def get_embedding_http_client() -> httpx.AsyncClient:
    """
    The process-wide embedding HTTP client, created on first call.
    """

    global _client

    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, limits=_LIMITS)

    return _client


async def close_embedding_http_client() -> None:
    """
    Close the shared client, if one was ever opened. Idempotent, so it is
    safe from a lifespan shutdown that may run without a matching startup.
    """

    global _client

    if _client is not None and not _client.is_closed:
        await _client.aclose()

    _client = None


async def warm_embedding_connection() -> None:
    """
    Open the pool's first connection at startup, rather than making the
    first caller of the day pay for it mid-turn.

    The docstring above measures a cold client at 0.88-1.60s against a warm
    one at 0.28-0.33s, and retrieval's whole per-turn budget is 1.5s. So the
    first turns after a restart lose their knowledge entirely and the
    assistant answers without it - measured immediately after a restart at
    3.06s and 1.79s, both abandoned, before the next call came back in
    1.33s and the one after in 0.16s.

    That is every deploy, not an edge case: the media plane and the API
    deploy separately and a restart is routine. Pre-warming is the first
    technique CLAUDE.md section 11 lists for keeping retrieval inside the
    budget, and this is the cheapest half of it.

    Deliberately best-effort and never raised. It runs detached from the
    lifespan so a slow or unreachable provider cannot hold up startup, and
    an embedding provider that is down must not stop the API serving
    everything that does not need it. Failure costs exactly what happens
    today: the first turn is slow.
    """

    # Imported here, not at module scope: the providers this builds import
    # this module for their shared client, so a top-level import is a cycle.
    from app.providers.factory import get_embedding_provider

    provider = get_embedding_provider()

    # Three, not one. One was measured and was not enough: the first calls
    # after a restart still ran 2.74s, 1.62s and 1.56s.
    #
    # Being honest about what this does and does not fix. It opens the pool
    # and gets the handshake out of the way, which the measurements at the
    # top of this module are worth. It does not keep the provider awake -
    # the hosted router goes cold again after an idle period, so by the time
    # a call arrives this has long since stopped helping. What covers the
    # first turn of a call is the session-start wake in
    # app/services/query_embedding_cache.py, which runs while the greeting
    # is playing. This is the cheap half, and it is only the cheap half.
    for attempt in range(_WARM_UP_CALLS):
        try:
            await provider.embed(["warm"])
        except Exception:
            logger.info(
                "embedding connection warm-up did not complete (call %d)",
                attempt + 1,
                exc_info=True,
            )

            return

    logger.info("embedding connection warmed with %d calls", _WARM_UP_CALLS)
