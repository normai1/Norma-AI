from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"

CREDENTIALS = {"email": "member@example.com", "password": "a-strong-password"}


async def _register(client: AsyncClient, **overrides: str) -> dict:
    payload = {**CREDENTIALS, **overrides}

    response = await client.post(REGISTER, json=payload)

    return response.json()


async def test_login_returns_tokens(client: AsyncClient) -> None:
    await _register(client)

    response = await client.post(LOGIN, json=CREDENTIALS)

    assert response.status_code == 200

    body = response.json()

    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["email"] == "member@example.com"


async def test_login_is_case_insensitive_on_email(client: AsyncClient) -> None:
    await _register(client)

    response = await client.post(
        LOGIN,
        json={"email": "MEMBER@Example.com", "password": CREDENTIALS["password"]},
    )

    assert response.status_code == 200


async def test_login_rejects_wrong_password(client: AsyncClient) -> None:
    await _register(client)

    response = await client.post(
        LOGIN,
        json={"email": CREDENTIALS["email"], "password": "not-the-password"},
    )

    assert response.status_code == 401


async def test_login_rejects_unknown_email(client: AsyncClient) -> None:
    response = await client.post(
        LOGIN,
        json={"email": "nobody@example.com", "password": "a-strong-password"},
    )

    assert response.status_code == 401


async def test_unknown_email_and_wrong_password_are_indistinguishable(
    client: AsyncClient,
) -> None:
    await _register(client)

    wrong_password = await client.post(
        LOGIN,
        json={"email": CREDENTIALS["email"], "password": "not-the-password"},
    )

    unknown_email = await client.post(
        LOGIN,
        json={"email": "nobody@example.com", "password": "a-strong-password"},
    )

    assert wrong_password.status_code == unknown_email.status_code
    assert wrong_password.json() == unknown_email.json()


async def test_login_rejects_deactivated_account(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _register(client)

    user = await db.scalar(select(User).where(User.email == CREDENTIALS["email"]))
    user.is_active = False
    await db.flush()

    response = await client.post(LOGIN, json=CREDENTIALS)

    assert response.status_code == 403


async def test_login_records_last_login(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _register(client, email="lastlogin@example.com")

    user = await db.scalar(select(User).where(User.email == "lastlogin@example.com"))

    assert user.last_login_at is not None
