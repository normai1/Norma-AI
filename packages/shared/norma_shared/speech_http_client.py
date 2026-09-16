"""
One kept-alive HTTP client for the speech providers, shared by every request
in the process.

`ElevenLabsTTS` was written to create an `httpx.AsyncClient` per call and
close it again. That is a fresh DNS lookup, TCP connect and TLS handshake to
a remote host before any audio can start - and text-to-speech is called once
per *sentence* while a reply streams, so a three-sentence answer paid it
three times.

Measured from the voice container against api.elevenlabs.io:

    a new client per call:   1358ms, 690ms, 513ms
    one kept-alive client:    488ms, 308ms, 288ms

Which shows up exactly where the turn metrics said it would. Across 200 real
turns, the first turn of a call ran 668ms of text-to-speech against 488ms on
later turns, and every other leg was slower on the first turn too - the
signature of connections being opened rather than reused.

This is the same fix, for the same reason, as
`apps/api/app/providers/embedding_http_client.py`, which was made for the
hop from the API to the embedding provider. Both planes talk to hosted
providers over HTTPS inside a latency budget; neither can afford a handshake
per request.

Deliberately here in the shared package rather than in `apps/voice`: the
provider that needs it lives here, and the control plane calls the same
class for voice previews.
"""

import httpx

# Small: this pool serves one host and the work is request-response. Keeping
# connections alive is the entire point, so the idle expiry has to outlast
# the gaps between sentences, between turns, and between calls - a worker
# holds this for its whole life.
_LIMITS = httpx.Limits(
    max_connections=10,
    max_keepalive_connections=10,
    keepalive_expiry=300.0,
)

_client: httpx.AsyncClient | None = None


def get_speech_http_client() -> httpx.AsyncClient:
    """
    The process-wide client for speech provider calls, created on first use.

    No default timeout: every caller here already passes its own, and a
    default would quietly become the one that applies if a caller ever
    stopped - on the audio path, where a request without a deadline is a
    call hanging on silence.
    """

    global _client

    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(limits=_LIMITS)

    return _client


async def close_speech_http_client() -> None:
    """
    Close the shared client, if one was ever opened. Idempotent, so it is
    safe from a shutdown path that may run without a matching startup.
    """

    global _client

    if _client is not None and not _client.is_closed:
        await _client.aclose()

    _client = None
