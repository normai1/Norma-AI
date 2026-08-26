import uuid

from httpx import AsyncClient

REGISTER = "/api/v1/auth/register"
ORGS = "/api/v1/organizations"


async def _signed_in(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        REGISTER,
        json={"email": email, "password": "a-strong-password"},
    )

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _org_with_owner(
    client: AsyncClient,
    email: str,
    name: str = "Test Org",
) -> tuple[dict[str, str], str]:
    headers = await _signed_in(client, email)
    created = await client.post(ORGS, json={"name": name}, headers=headers)

    return headers, created.json()["id"]


async def test_update_organization_name(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "rename@example.com")

    response = await client.patch(
        f"{ORGS}/{org_id}",
        json={"name": "Renamed Co"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Co"


async def test_update_settings_without_touching_name(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "settings@example.com")

    response = await client.patch(
        f"{ORGS}/{org_id}",
        json={"settings": {"timezone": "Asia/Kolkata"}},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["settings"] == {"timezone": "Asia/Kolkata"}
    assert response.json()["name"] == "Test Org"


async def test_update_rejects_non_member(client: AsyncClient) -> None:
    _, org_id = await _org_with_owner(client, "owner-u@example.com")
    outsider = await _signed_in(client, "outsider-u@example.com")

    response = await client.patch(
        f"{ORGS}/{org_id}",
        json={"name": "Hijacked"},
        headers=outsider,
    )

    assert response.status_code == 404


async def test_list_members_returns_the_owner(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "roster@example.com")

    response = await client.get(f"{ORGS}/{org_id}/members", headers=headers)

    assert response.status_code == 200

    members = response.json()

    assert len(members) == 1
    assert members[0]["role"] == "owner"
    assert members[0]["user"]["email"] == "roster@example.com"
    assert "password_hash" not in members[0]["user"]


async def test_list_members_rejects_non_member(client: AsyncClient) -> None:
    _, org_id = await _org_with_owner(client, "roster-owner@example.com")
    outsider = await _signed_in(client, "roster-outsider@example.com")

    response = await client.get(f"{ORGS}/{org_id}/members", headers=outsider)

    assert response.status_code == 404


async def test_sole_owner_cannot_demote_themselves(client: AsyncClient) -> None:
    """
    Refused as a self-role-change (403) before the last-owner guard is reached.

    Demotion can no longer strand an organization at all: only an owner may
    demote an owner and nobody may change their own role, so demoting one
    requires a second to exist. The 409 guard still covers self-removal, which
    `test_cannot_remove_the_last_owner` exercises.
    """

    headers, org_id = await _org_with_owner(client, "lastowner@example.com")

    roster = await client.get(f"{ORGS}/{org_id}/members", headers=headers)
    member_id = roster.json()[0]["id"]

    response = await client.patch(
        f"{ORGS}/{org_id}/members/{member_id}",
        json={"role": "member"},
        headers=headers,
    )

    assert response.status_code == 403


async def test_cannot_remove_the_last_owner(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "lastowner2@example.com")

    roster = await client.get(f"{ORGS}/{org_id}/members", headers=headers)
    member_id = roster.json()[0]["id"]

    response = await client.delete(
        f"{ORGS}/{org_id}/members/{member_id}",
        headers=headers,
    )

    assert response.status_code == 409


async def test_change_role_rejects_unknown_member(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "unknownmember@example.com")

    response = await client.patch(
        f"{ORGS}/{org_id}/members/{uuid.uuid4()}",
        json={"role": "admin"},
        headers=headers,
    )

    assert response.status_code == 404


async def test_change_role_rejects_an_invalid_role(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "badrole@example.com")

    roster = await client.get(f"{ORGS}/{org_id}/members", headers=headers)
    member_id = roster.json()[0]["id"]

    response = await client.patch(
        f"{ORGS}/{org_id}/members/{member_id}",
        json={"role": "superuser"},
        headers=headers,
    )

    assert response.status_code == 422


async def test_change_role_rejects_roles_that_merely_contain_a_valid_one(
    client: AsyncClient,
) -> None:
    """
    Regression: the role field was once an unanchored regex, so near-misses
    like "xowner" passed validation and only failed at the database CHECK,
    surfacing as a 500 instead of a 422.
    """

    headers, org_id = await _org_with_owner(client, "nearmiss@example.com")

    roster = await client.get(f"{ORGS}/{org_id}/members", headers=headers)
    member_id = roster.json()[0]["id"]

    for candidate in ("xowner", "ownerX", "admin!!", ""):
        response = await client.patch(
            f"{ORGS}/{org_id}/members/{member_id}",
            json={"role": candidate},
            headers=headers,
        )

        assert response.status_code == 422, candidate


async def test_member_management_requires_authentication(
    client: AsyncClient,
) -> None:
    org_id = uuid.uuid4()

    assert (await client.get(f"{ORGS}/{org_id}/members")).status_code == 401
    assert (
        await client.patch(f"{ORGS}/{org_id}", json={"name": "x"})
    ).status_code == 401
