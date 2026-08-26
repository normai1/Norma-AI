from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

REGISTER = "/api/v1/auth/register"


async def test_register_creates_account_and_returns_tokens(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER,
        json={
            "email": "New.User@Example.com",
            "password": "a-strong-password",
            "full_name": "New User",
        },
    )

    assert response.status_code == 201

    body = response.json()

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "new.user@example.com"
    assert body["user"]["full_name"] == "New User"
    assert body["user"]["is_active"] is True
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]


async def test_register_stores_hashed_password(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await client.post(
        REGISTER,
        json={"email": "hashed@example.com", "password": "a-strong-password"},
    )

    user = await db.scalar(select(User).where(User.email == "hashed@example.com"))

    assert user is not None
    assert user.password_hash != "a-strong-password"
    assert user.password_hash.startswith("$argon2")


async def test_register_rejects_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "dupe@example.com", "password": "a-strong-password"}

    first = await client.post(REGISTER, json=payload)
    second = await client.post(REGISTER, json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


async def test_register_duplicate_is_case_insensitive(client: AsyncClient) -> None:
    await client.post(
        REGISTER,
        json={"email": "casing@example.com", "password": "a-strong-password"},
    )

    response = await client.post(
        REGISTER,
        json={"email": "CASING@Example.COM", "password": "a-strong-password"},
    )

    assert response.status_code == 409


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER,
        json={"email": "short@example.com", "password": "tiny"},
    )

    assert response.status_code == 422


async def test_register_rejects_malformed_email(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER,
        json={"email": "not-an-email", "password": "a-strong-password"},
    )

    assert response.status_code == 422


async def test_register_rejects_missing_fields(client: AsyncClient) -> None:
    response = await client.post(REGISTER, json={"email": "nopass@example.com"})

    assert response.status_code == 422
