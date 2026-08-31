import jwt
import pytest
from norma_shared.voice_session_ticket import (
    InvalidVoiceSessionTicket,
    create_voice_session_ticket,
    decode_voice_session_ticket,
)

_SECRET = "test-secret-key"
_ALGORITHM = "HS256"
_ASSISTANT_ID = "assistant-123"


def _ticket(*, secret_key: str = _SECRET, ttl_seconds: float = 60) -> str:
    return create_voice_session_ticket(
        secret_key=secret_key,
        algorithm=_ALGORITHM,
        assistant_id=_ASSISTANT_ID,
        ttl_seconds=ttl_seconds,
    )


def _decode(ticket: str, *, secret_key: str = _SECRET) -> str:
    return decode_voice_session_ticket(
        ticket, secret_key=secret_key, algorithm=_ALGORITHM
    )


def test_ticket_round_trips_to_the_same_assistant_id() -> None:
    assert _decode(_ticket()) == _ASSISTANT_ID


def test_ticket_decoded_with_the_wrong_secret_is_rejected() -> None:
    with pytest.raises(InvalidVoiceSessionTicket):
        _decode(_ticket(), secret_key="wrong-secret")


def test_expired_ticket_is_rejected() -> None:
    with pytest.raises(InvalidVoiceSessionTicket):
        _decode(_ticket(ttl_seconds=-1))


def test_token_of_the_wrong_type_claim_is_rejected() -> None:
    other_token = jwt.encode(
        {"sub": "user-123", "type": "access", "exp": 9999999999},
        _SECRET,
        algorithm=_ALGORITHM,
    )

    with pytest.raises(InvalidVoiceSessionTicket):
        _decode(other_token)


def test_tampered_signature_is_rejected() -> None:
    ticket = _ticket()
    # Flip a character well inside the signature segment, not the very last
    # one - the last base64url character of a JWT can have redundant bits
    # depending on the token's length, which occasionally leaves a
    # single-bit-flipped last character still valid and makes the test flaky.
    tampered = ticket[:-5] + ("A" if ticket[-5] != "A" else "B") + ticket[-4:]

    with pytest.raises(InvalidVoiceSessionTicket):
        _decode(tampered)


def test_token_missing_assistant_id_claim_is_rejected() -> None:
    other_token = jwt.encode(
        {"type": "voice_session", "exp": 9999999999}, _SECRET, algorithm=_ALGORITHM
    )

    with pytest.raises(InvalidVoiceSessionTicket):
        _decode(other_token)
