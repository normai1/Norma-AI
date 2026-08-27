import uuid

from httpx import AsyncClient

from tests.conftest import _signed_in

REGISTER = "/api/v1/auth/register"
ORGS = "/api/v1/organizations"


async def test_create_returns_201_with_owner_role(client: AsyncClient) -> None:
    headers = await _signed_in(client, "creator@example.com")

    response = await client.post(ORGS, json={"name": "Acme Corp"}, headers=headers)

    assert response.status_code == 201

    body = response.json()

    assert body["name"] == "Acme Corp"
    assert body["slug"] == "acme-corp"
    assert body["role"] == "owner"
    assert body["status"] == "active"
    assert body["settings"] == {}


async def test_create_rejects_missing_name(client: AsyncClient) -> None:
    headers = await _signed_in(client, "noname@example.com")

    assert (await client.post(ORGS, json={}, headers=headers)).status_code == 422


async def test_create_rejects_empty_name(client: AsyncClient) -> None:
    headers = await _signed_in(client, "emptyname@example.com")

    response = await client.post(ORGS, json={"name": ""}, headers=headers)

    assert response.status_code == 422


async def test_list_returns_only_own_organizations(client: AsyncClient) -> None:
    mine = await _signed_in(client, "listmine@example.com")
    theirs = await _signed_in(client, "listtheirs@example.com")

    await client.post(ORGS, json={"name": "Mine"}, headers=mine)
    await client.post(ORGS, json={"name": "Theirs"}, headers=theirs)

    response = await client.get(ORGS, headers=mine)

    assert response.status_code == 200
    assert [org["name"] for org in response.json()] == ["Mine"]


async def test_list_is_empty_for_a_new_user(client: AsyncClient) -> None:
    headers = await _signed_in(client, "brandnew@example.com")

    response = await client.get(ORGS, headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_get_returns_organization_for_a_member(client: AsyncClient) -> None:
    headers = await _signed_in(client, "member@example.com")

    created = await client.post(ORGS, json={"name": "Visible"}, headers=headers)
    organization_id = created.json()["id"]

    response = await client.get(f"{ORGS}/{organization_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == organization_id
    assert response.json()["role"] == "owner"


async def test_get_returns_404_for_another_users_organization(
    client: AsyncClient,
) -> None:
    owner = await _signed_in(client, "hidden-owner@example.com")
    outsider = await _signed_in(client, "hidden-outsider@example.com")

    created = await client.post(ORGS, json={"name": "Private"}, headers=owner)
    organization_id = created.json()["id"]

    response = await client.get(f"{ORGS}/{organization_id}", headers=outsider)

    assert response.status_code == 404


async def test_nonexistent_and_forbidden_are_indistinguishable(
    client: AsyncClient,
) -> None:
    owner = await _signed_in(client, "probe-owner@example.com")
    outsider = await _signed_in(client, "probe-outsider@example.com")

    created = await client.post(ORGS, json={"name": "Probe"}, headers=owner)

    forbidden = await client.get(
        f"{ORGS}/{created.json()['id']}",
        headers=outsider,
    )
    missing = await client.get(f"{ORGS}/{uuid.uuid4()}", headers=outsider)

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()


async def test_get_rejects_a_malformed_organization_id(
    client: AsyncClient,
) -> None:
    headers = await _signed_in(client, "malformed@example.com")

    response = await client.get(f"{ORGS}/not-a-uuid", headers=headers)

    assert response.status_code == 422


async def test_all_routes_require_authentication(client: AsyncClient) -> None:
    assert (await client.post(ORGS, json={"name": "Nope"})).status_code == 401
    assert (await client.get(ORGS)).status_code == 401
    assert (await client.get(f"{ORGS}/{uuid.uuid4()}")).status_code == 401
