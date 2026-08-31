"""
Short-lived voice-session ticket (item 21a) - the sole authorization
mechanism for a browser test call reaching apps/voice's /media/session
route. Lives here, not duplicated per plane, because the claim shape and
signing algorithm must match byte-for-byte between issuer (apps/api) and
verifier (apps/voice) for a ticket to ever validate - unlike
TTSConfig/LLMConfig's deliberate small duplication, which carries no such
cross-plane agreement requirement.
"""

import time

import jwt

VOICE_SESSION_TICKET_TYPE = "voice_session"


class InvalidVoiceSessionTicket(Exception):
    """
    Raised for any ticket that must not be trusted: expired, tampered,
    wrong signing secret, wrong type claim, or missing/malformed claims.
    Deliberately a single exception type with no further detail - the
    caller (apps/voice, before accepting a WebSocket) must treat every
    rejection reason identically, matching workspace_deps.py's own
    established information-hiding precedent.
    """


def create_voice_session_ticket(
    *,
    secret_key: str,
    algorithm: str,
    assistant_id: str,
    ttl_seconds: float,
) -> str:
    """Encode a ticket authorizing exactly one assistant_id, expiring after ttl_seconds."""

    now = time.time()
    payload = {
        "assistant_id": assistant_id,
        "type": VOICE_SESSION_TICKET_TYPE,
        "iat": now,
        "exp": now + ttl_seconds,
    }

    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_voice_session_ticket(
    ticket: str,
    *,
    secret_key: str,
    algorithm: str,
) -> str:
    """
    Decode a ticket and return its assistant_id, or raise
    InvalidVoiceSessionTicket for any reason the ticket must not be
    trusted. Never lets a raw PyJWT exception escape, so a caller can
    catch failures without importing PyJWT itself.
    """

    try:
        payload = jwt.decode(ticket, secret_key, algorithms=[algorithm])
    except jwt.InvalidTokenError as exc:
        raise InvalidVoiceSessionTicket("Invalid or expired voice session ticket") from exc

    if payload.get("type") != VOICE_SESSION_TICKET_TYPE:
        raise InvalidVoiceSessionTicket("Unexpected token type")

    assistant_id = payload.get("assistant_id")

    if not isinstance(assistant_id, str) or not assistant_id:
        raise InvalidVoiceSessionTicket("Missing assistant_id claim")

    return assistant_id
