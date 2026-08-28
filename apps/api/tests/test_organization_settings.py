from httpx import AsyncClient

from tests.conftest import _org_with_owner, _signed_in

ORGANIZATIONS = "/api/v1/organizations"


async def test_new_organization_has_validated_default_settings(
    client: AsyncClient,
) -> None:
    headers = await _signed_in(client, "settings-created-default@example.com")

    response = await client.post(
        ORGANIZATIONS,
        json={"name": "Fresh Org"},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["settings"] == {"currency": "USD"}


async def test_valid_currency_persists(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "settings-currency@example.com")

    response = await client.patch(
        f"{ORGANIZATIONS}/{org_id}",
        json={"settings": {"currency": "EUR"}},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["settings"]["currency"] == "EUR"

    fetched = await client.get(f"{ORGANIZATIONS}/{org_id}", headers=headers)

    assert fetched.json()["settings"]["currency"] == "EUR"


async def test_unsupported_currency_is_rejected(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "settings-bad-currency@example.com")

    response = await client.patch(
        f"{ORGANIZATIONS}/{org_id}",
        json={"settings": {"currency": "ZZZ"}},
        headers=headers,
    )

    assert response.status_code == 422


async def test_name_only_update_leaves_currency_untouched(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "settings-name-only@example.com")

    await client.patch(
        f"{ORGANIZATIONS}/{org_id}",
        json={"settings": {"currency": "GBP"}},
        headers=headers,
    )

    response = await client.patch(
        f"{ORGANIZATIONS}/{org_id}",
        json={"name": "Renamed Org"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed Org"
    assert body["settings"]["currency"] == "GBP"


async def test_settings_only_update_leaves_name_untouched(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(
        client,
        "settings-currency-only@example.com",
        name="Original Name",
    )

    response = await client.patch(
        f"{ORGANIZATIONS}/{org_id}",
        json={"settings": {"currency": "CAD"}},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Original Name"
    assert body["settings"]["currency"] == "CAD"
