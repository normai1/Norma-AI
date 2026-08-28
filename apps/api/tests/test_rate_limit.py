import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.config import settings
from app.core.rate_limit import (
    LOGIN_RATE_LIMIT,
    PASSWORD_CHANGE_RATE_LIMIT,
    REGISTER_RATE_LIMIT,
    RateLimitExceeded,
    RateLimitRule,
    enforce,
)

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
CHANGE_PASSWORD = "/api/v1/auth/me/password"

CREDENTIALS = {"email": "limited@example.com", "password": "a-strong-password"}


async def test_enforce_allows_up_to_the_limit(redis_client: Redis) -> None:
    rule = RateLimitRule(limit=3, window_seconds=60)

    for _ in range(3):
        await enforce(redis_client, "unit:allow", rule)


async def test_enforce_blocks_past_the_limit(redis_client: Redis) -> None:
    rule = RateLimitRule(limit=2, window_seconds=60)

    await enforce(redis_client, "unit:block", rule)
    await enforce(redis_client, "unit:block", rule)

    with pytest.raises(RateLimitExceeded) as exc_info:
        await enforce(redis_client, "unit:block", rule)

    assert exc_info.value.retry_after > 0


async def test_enforce_sets_an_expiry(redis_client: Redis) -> None:
    rule = RateLimitRule(limit=5, window_seconds=60)

    await enforce(redis_client, "unit:ttl", rule)

    assert await redis_client.ttl("ratelimit:unit:ttl") > 0


async def test_enforce_repairs_a_counter_left_without_an_expiry(
    redis_client: Redis,
) -> None:
    rule = RateLimitRule(limit=5, window_seconds=60)

    # Stand in for a crash between INCR and EXPIRE under the old two-call code.
    await redis_client.set("ratelimit:unit:orphan", 1)

    assert await redis_client.ttl("ratelimit:unit:orphan") == -1

    await enforce(redis_client, "unit:orphan", rule)

    assert await redis_client.ttl("ratelimit:unit:orphan") > 0


async def test_enforce_does_not_extend_an_existing_window(
    redis_client: Redis,
) -> None:
    rule = RateLimitRule(limit=5, window_seconds=60)

    await enforce(redis_client, "unit:fixed", rule)
    await redis_client.expire("ratelimit:unit:fixed", 5)

    await enforce(redis_client, "unit:fixed", rule)

    # A sliding window would have pushed this back to the full 60 seconds.
    assert await redis_client.ttl("ratelimit:unit:fixed") <= 5


async def test_enforce_counts_keys_independently(redis_client: Redis) -> None:
    rule = RateLimitRule(limit=1, window_seconds=60)

    await enforce(redis_client, "unit:first", rule)
    await enforce(redis_client, "unit:second", rule)

    with pytest.raises(RateLimitExceeded):
        await enforce(redis_client, "unit:first", rule)


async def test_login_blocks_after_repeated_failures(client: AsyncClient) -> None:
    await client.post(REGISTER, json=CREDENTIALS)

    wrong = {"email": CREDENTIALS["email"], "password": "not-the-password"}

    for _ in range(LOGIN_RATE_LIMIT.limit):
        await client.post(LOGIN, json=wrong)

    blocked = await client.post(LOGIN, json=wrong)

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_rate_limited_login_rejects_even_the_right_password(
    client: AsyncClient,
) -> None:
    await client.post(REGISTER, json=CREDENTIALS)

    wrong = {"email": CREDENTIALS["email"], "password": "not-the-password"}

    for _ in range(LOGIN_RATE_LIMIT.limit):
        await client.post(LOGIN, json=wrong)

    blocked = await client.post(LOGIN, json=CREDENTIALS)

    assert blocked.status_code == 429


async def test_login_limit_is_scoped_per_account(client: AsyncClient) -> None:
    await client.post(REGISTER, json=CREDENTIALS)
    await client.post(
        REGISTER,
        json={"email": "other@example.com", "password": "a-strong-password"},
    )

    wrong = {"email": CREDENTIALS["email"], "password": "not-the-password"}

    for _ in range(LOGIN_RATE_LIMIT.limit + 1):
        await client.post(LOGIN, json=wrong)

    # The spray against one account must not lock a different one out.
    other = await client.post(
        LOGIN,
        json={"email": "other@example.com", "password": "a-strong-password"},
    )

    assert other.status_code == 200


async def test_register_limit_is_per_client_behind_a_proxy(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Behind a proxy every request shares one peer address. If the limit keyed on
    that, a handful of signups would lock out the whole product.
    """

    monkeypatch.setattr(settings, "trusted_proxy_count", 1)

    noisy = {"x-forwarded-for": "203.0.113.7"}

    for index in range(REGISTER_RATE_LIMIT.limit + 1):
        await client.post(
            REGISTER,
            json={
                "email": f"noisy{index}@example.com",
                "password": "a-strong-password",
            },
            headers=noisy,
        )

    exhausted = await client.post(
        REGISTER,
        json={"email": "noisy-again@example.com", "password": "a-strong-password"},
        headers=noisy,
    )

    assert exhausted.status_code == 429

    someone_else = await client.post(
        REGISTER,
        json={"email": "different@example.com", "password": "a-strong-password"},
        headers={"x-forwarded-for": "198.51.100.4"},
    )

    assert someone_else.status_code == 201


async def test_register_blocks_after_repeated_signups(client: AsyncClient) -> None:
    for index in range(REGISTER_RATE_LIMIT.limit):
        response = await client.post(
            REGISTER,
            json={
                "email": f"signup{index}@example.com",
                "password": "a-strong-password",
            },
        )

        assert response.status_code == 201

    blocked = await client.post(
        REGISTER,
        json={"email": "one-too-many@example.com", "password": "a-strong-password"},
    )

    assert blocked.status_code == 429


async def _password_change_headers(client: AsyncClient, email: str) -> dict:
    response = await client.post(
        REGISTER,
        json={"email": email, "password": "a-strong-password"},
    )

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_password_change_allows_a_successful_change_within_the_limit(
    client: AsyncClient,
) -> None:
    headers = await _password_change_headers(client, "pwlimit-ok@example.com")

    response = await client.post(
        CHANGE_PASSWORD,
        json={
            "current_password": "a-strong-password",
            "new_password": "a-new-strong-password",
        },
        headers=headers,
    )

    assert response.status_code == 200


async def test_password_change_blocks_after_repeated_attempts(
    client: AsyncClient,
) -> None:
    headers = await _password_change_headers(client, "pwlimit-block@example.com")

    wrong = {
        "current_password": "not-the-real-password",
        "new_password": "a-new-strong-password",
    }

    for _ in range(PASSWORD_CHANGE_RATE_LIMIT.limit):
        await client.post(CHANGE_PASSWORD, json=wrong, headers=headers)

    blocked = await client.post(CHANGE_PASSWORD, json=wrong, headers=headers)

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_password_change_limit_is_scoped_per_user(client: AsyncClient) -> None:
    headers = await _password_change_headers(client, "pwlimit-scope-a@example.com")
    other_headers = await _password_change_headers(
        client,
        "pwlimit-scope-b@example.com",
    )

    wrong = {
        "current_password": "not-the-real-password",
        "new_password": "a-new-strong-password",
    }

    for _ in range(PASSWORD_CHANGE_RATE_LIMIT.limit + 1):
        await client.post(CHANGE_PASSWORD, json=wrong, headers=headers)

    # Attempts against one user must not lock a different one out.
    other = await client.post(
        CHANGE_PASSWORD,
        json={
            "current_password": "a-strong-password",
            "new_password": "a-new-strong-password",
        },
        headers=other_headers,
    )

    assert other.status_code == 200
