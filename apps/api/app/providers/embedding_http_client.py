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

import httpx

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
