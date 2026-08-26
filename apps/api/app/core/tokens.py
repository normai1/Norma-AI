from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings

ACCESS_TOKEN_TYPE = "access"


def create_access_token(subject: str) -> tuple[str, int]:
    """
    Issue a signed access token for a user id. Returns the token and its lifetime.
    """

    expires_in = settings.access_token_expire_minutes * 60
    issued_at = datetime.now(UTC)

    payload = {
        "sub": subject,
        "type": ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "exp": issued_at + timedelta(seconds=expires_in),
    }

    token = jwt.encode(
        payload,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    return token, expires_in


def decode_access_token(token: str) -> dict:
    """
    Decode and validate an access token, raising jwt.PyJWTError when invalid.
    """

    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    # A refresh token must never be accepted where an access token is expected.
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Unexpected token type")

    return payload


def refresh_token_expiry() -> datetime:
    """
    Absolute expiry for a newly issued refresh token.
    """

    return datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
