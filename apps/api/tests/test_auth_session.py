from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_token
from app.models.session import UserSession

REGISTER = "/api/v1/auth/register"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"

CREDENTIALS = {"email": "session@example.com", "password": "a-strong-password"}


async def _register(client: AsyncClient, **overrides: str) -> dict:
    response = await client.post(REGISTER, json={**CREDENTIALS, **overrides})

    return response.json()


async def test_me_returns_profile_for_valid_token(client: AsyncClient) -> None:
    tokens = await _register(client)

    response = await client.get(
        ME,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "session@example.com"


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get(ME)

    assert response.status_code == 401


async def test_me_rejects_a_garbage_token(client: AsyncClient) -> None:
    response = await client.get(ME, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401


async def test_me_rejects_a_refresh_token(client: AsyncClient) -> None:
    tokens = await _register(client, email="wrongtype@example.com")

    response = await client.get(
        ME,
        headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
    )

    assert response.status_code == 401


async def test_me_rejects_an_expired_access_token(client: AsyncClient) -> None:
    tokens = await _register(client, email="expiredaccess@example.com")

    claims = jwt.decode(
        tokens["access_token"],
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    expired = jwt.encode(
        {**claims, "exp": datetime.now(UTC) - timedelta(seconds=1)},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get(
        ME,
        headers={"Authorization": f"Bearer {expired}"},
    )

    assert response.status_code == 401


async def test_a_freshly_signed_token_with_the_same_claims_is_accepted(
    client: AsyncClient,
) -> None:
    """
    Discriminator for the test above: proves the 401 came from the expiry and
    not from re-signing the token.
    """

    tokens = await _register(client, email="resigned@example.com")

    claims = jwt.decode(
        tokens["access_token"],
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    resigned = jwt.encode(
        claims,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    response = await client.get(
        ME,
        headers={"Authorization": f"Bearer {resigned}"},
    )

    assert response.status_code == 200


async def test_refresh_returns_a_new_pair(client: AsyncClient) -> None:
    tokens = await _register(client, email="rotate@example.com")

    response = await client.post(
        REFRESH,
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert response.status_code == 200

    body = response.json()

    assert body["refresh_token"] != tokens["refresh_token"]
    assert body["user"]["email"] == "rotate@example.com"


async def test_refresh_revokes_the_used_token(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    tokens = await _register(client, email="revoked@example.com")

    await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})

    used = await db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(tokens["refresh_token"]),
        )
    )

    assert used is not None
    assert used.revoked_at is not None


async def test_replaying_a_used_refresh_token_fails(client: AsyncClient) -> None:
    tokens = await _register(client, email="replay@example.com")

    await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})

    replay = await client.post(
        REFRESH,
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert replay.status_code == 401


async def test_replay_revokes_every_session_for_that_user(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    tokens = await _register(client, email="breach@example.com")

    rotated = await client.post(
        REFRESH,
        json={"refresh_token": tokens["refresh_token"]},
    )
    live_token = rotated.json()["refresh_token"]

    # Replaying the already-rotated token looks like a stolen credential.
    await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})

    reuse_after_breach = await client.post(
        REFRESH,
        json={"refresh_token": live_token},
    )

    assert reuse_after_breach.status_code == 401


async def test_refresh_rejects_an_unknown_token(client: AsyncClient) -> None:
    response = await client.post(REFRESH, json={"refresh_token": "no-such-token"})

    assert response.status_code == 401


async def test_refresh_rejects_an_expired_token(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    tokens = await _register(client, email="expired@example.com")

    session = await db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(tokens["refresh_token"]),
        )
    )
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.flush()

    response = await client.post(
        REFRESH,
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert response.status_code == 401


async def test_logout_revokes_the_session(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    tokens = await _register(client, email="logout@example.com")

    response = await client.post(
        LOGOUT,
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert response.status_code == 204

    session = await db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(tokens["refresh_token"]),
        )
    )

    assert session.revoked_at is not None


async def test_refresh_after_logout_fails(client: AsyncClient) -> None:
    tokens = await _register(client, email="logout-then-refresh@example.com")

    await client.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]})

    response = await client.post(
        REFRESH,
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert response.status_code == 401


async def test_logout_with_unknown_token_still_succeeds(client: AsyncClient) -> None:
    response = await client.post(LOGOUT, json={"refresh_token": "no-such-token"})

    assert response.status_code == 204
