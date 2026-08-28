from httpx import AsyncClient

from tests.conftest import _signed_in

ME = "/api/v1/auth/me"
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
CHANGE_PASSWORD = "/api/v1/auth/me/password"

ORIGINAL_PASSWORD = "a-strong-password"


async def test_update_sets_full_name(client: AsyncClient) -> None:
    headers = await _signed_in(client, "profile-name@example.com")

    response = await client.patch(
        ME,
        json={"full_name": "Jane Doe"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Jane Doe"

    fetched = await client.get(ME, headers=headers)

    assert fetched.json()["full_name"] == "Jane Doe"


async def test_update_sets_avatar_url(client: AsyncClient) -> None:
    headers = await _signed_in(client, "profile-avatar@example.com")

    response = await client.patch(
        ME,
        json={"avatar_url": "https://example.com/avatar.png"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["avatar_url"] == "https://example.com/avatar.png"


async def test_explicit_null_clears_full_name(client: AsyncClient) -> None:
    headers = await _signed_in(client, "profile-clear@example.com")

    await client.patch(ME, json={"full_name": "Set First"}, headers=headers)
    response = await client.patch(ME, json={"full_name": None}, headers=headers)

    assert response.status_code == 200
    assert response.json()["full_name"] is None


async def test_omitted_avatar_url_is_left_untouched(client: AsyncClient) -> None:
    headers = await _signed_in(client, "profile-untouched@example.com")

    await client.patch(
        ME,
        json={"avatar_url": "https://example.com/avatar.png"},
        headers=headers,
    )
    response = await client.patch(ME, json={"full_name": "Only Name"}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Only Name"
    assert body["avatar_url"] == "https://example.com/avatar.png"


async def test_rejects_a_non_url_avatar(client: AsyncClient) -> None:
    headers = await _signed_in(client, "profile-bad-avatar@example.com")

    response = await client.patch(
        ME,
        json={"avatar_url": "not-a-url"},
        headers=headers,
    )

    assert response.status_code == 422


async def test_rejects_an_over_length_full_name(client: AsyncClient) -> None:
    headers = await _signed_in(client, "profile-long-name@example.com")

    response = await client.patch(
        ME,
        json={"full_name": "x" * 256},
        headers=headers,
    )

    assert response.status_code == 422


async def test_update_requires_authentication(client: AsyncClient) -> None:
    response = await client.patch(ME, json={"full_name": "Nope"})

    assert response.status_code == 401


async def _register(client: AsyncClient, email: str) -> dict:
    response = await client.post(
        REGISTER,
        json={"email": email, "password": ORIGINAL_PASSWORD},
    )

    return response.json()


def _headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_change_password_returns_a_working_new_refresh_token(
    client: AsyncClient,
) -> None:
    tokens = await _register(client, "password-change@example.com")

    response = await client.post(
        CHANGE_PASSWORD,
        json={
            "current_password": ORIGINAL_PASSWORD,
            "new_password": "a-new-strong-password",
        },
        headers=_headers(tokens),
    )

    assert response.status_code == 200

    new_tokens = response.json()

    refreshed = await client.post(
        REFRESH,
        json={"refresh_token": new_tokens["refresh_token"]},
    )

    assert refreshed.status_code == 200


async def test_change_password_rejects_a_wrong_current_password(
    client: AsyncClient,
) -> None:
    tokens = await _register(client, "password-wrong-current@example.com")

    response = await client.post(
        CHANGE_PASSWORD,
        json={
            "current_password": "not-the-real-password",
            "new_password": "a-new-strong-password",
        },
        headers=_headers(tokens),
    )

    assert response.status_code == 401

    # The password must be unchanged - the original still logs in.
    login = await client.post(
        LOGIN,
        json={
            "email": "password-wrong-current@example.com",
            "password": ORIGINAL_PASSWORD,
        },
    )

    assert login.status_code == 200


async def test_change_password_rejects_a_no_op_change(client: AsyncClient) -> None:
    tokens = await _register(client, "password-no-op@example.com")

    response = await client.post(
        CHANGE_PASSWORD,
        json={
            "current_password": ORIGINAL_PASSWORD,
            "new_password": ORIGINAL_PASSWORD,
        },
        headers=_headers(tokens),
    )

    assert response.status_code == 400


async def test_change_password_rejects_a_short_new_password(
    client: AsyncClient,
) -> None:
    tokens = await _register(client, "password-too-short@example.com")

    response = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": ORIGINAL_PASSWORD, "new_password": "short"},
        headers=_headers(tokens),
    )

    assert response.status_code == 422


async def test_change_password_revokes_every_prior_session(
    client: AsyncClient,
) -> None:
    tokens = await _register(client, "password-revoke@example.com")

    # A second session for the same account, e.g. a different device.
    second_login = await client.post(
        LOGIN,
        json={
            "email": "password-revoke@example.com",
            "password": ORIGINAL_PASSWORD,
        },
    )
    second_tokens = second_login.json()

    await client.post(
        CHANGE_PASSWORD,
        json={
            "current_password": ORIGINAL_PASSWORD,
            "new_password": "a-new-strong-password",
        },
        headers=_headers(tokens),
    )

    original_refresh = await client.post(
        REFRESH,
        json={"refresh_token": tokens["refresh_token"]},
    )
    second_refresh = await client.post(
        REFRESH,
        json={"refresh_token": second_tokens["refresh_token"]},
    )

    assert original_refresh.status_code == 401
    assert second_refresh.status_code == 401


async def test_can_log_in_with_the_new_password_and_not_the_old(
    client: AsyncClient,
) -> None:
    tokens = await _register(client, "password-relogin@example.com")

    await client.post(
        CHANGE_PASSWORD,
        json={
            "current_password": ORIGINAL_PASSWORD,
            "new_password": "a-new-strong-password",
        },
        headers=_headers(tokens),
    )

    old_login = await client.post(
        LOGIN,
        json={"email": "password-relogin@example.com", "password": ORIGINAL_PASSWORD},
    )
    new_login = await client.post(
        LOGIN,
        json={
            "email": "password-relogin@example.com",
            "password": "a-new-strong-password",
        },
    )

    assert old_login.status_code == 401
    assert new_login.status_code == 200


async def test_change_password_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": "x", "new_password": "a-new-strong-password"},
    )

    assert response.status_code == 401
