from httpx import AsyncClient

from tests.conftest import _org_with_owner

ORGS = "/api/v1/organizations"


def _workspaces_url(organization_id: str) -> str:
    return f"{ORGS}/{organization_id}/workspaces"


async def _create_workspace(
    client: AsyncClient,
    organization_id: str,
    owner_headers: dict[str, str],
    name: str = "Test Workspace",
) -> dict:
    response = await client.post(
        _workspaces_url(organization_id),
        json={"name": name},
        headers=owner_headers,
    )

    return response.json()


async def test_new_workspace_has_validated_default_settings(
    client: AsyncClient,
) -> None:
    owner_headers, org_id = await _org_with_owner(
        client,
        "ws-settings-created-default@example.com",
    )

    response = await client.post(
        _workspaces_url(org_id),
        json={"name": "Fresh Workspace"},
        headers=owner_headers,
    )

    assert response.status_code == 201
    assert response.json()["settings"] == {
        "timezone": "UTC",
        "locale": "en-US",
        "business_hours": None,
    }


async def test_valid_timezone_persists(client: AsyncClient) -> None:
    owner_headers, org_id = await _org_with_owner(client, "ws-settings-tz@example.com")
    workspace = await _create_workspace(client, org_id, owner_headers)

    response = await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={"settings": {"timezone": "America/Chicago"}},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["settings"]["timezone"] == "America/Chicago"

    fetched = await client.get(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        headers=owner_headers,
    )

    assert fetched.json()["settings"]["timezone"] == "America/Chicago"


async def test_unknown_timezone_is_rejected(client: AsyncClient) -> None:
    owner_headers, org_id = await _org_with_owner(
        client,
        "ws-settings-bad-tz@example.com",
    )
    workspace = await _create_workspace(client, org_id, owner_headers)

    response = await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={"settings": {"timezone": "Mars/Olympus_Mons"}},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_malformed_locale_is_rejected(client: AsyncClient) -> None:
    owner_headers, org_id = await _org_with_owner(
        client,
        "ws-settings-bad-locale@example.com",
    )
    workspace = await _create_workspace(client, org_id, owner_headers)

    response = await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={"settings": {"locale": "english"}},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_business_hours_close_before_open_is_rejected(
    client: AsyncClient,
) -> None:
    owner_headers, org_id = await _org_with_owner(
        client,
        "ws-settings-bad-hours@example.com",
    )
    workspace = await _create_workspace(client, org_id, owner_headers)

    response = await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={
            "settings": {
                "business_hours": {
                    "monday": {"open": "17:00", "close": "09:00"},
                },
            },
        },
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_unknown_business_hours_day_is_rejected(client: AsyncClient) -> None:
    owner_headers, org_id = await _org_with_owner(
        client,
        "ws-settings-bad-day@example.com",
    )
    workspace = await _create_workspace(client, org_id, owner_headers)

    response = await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={
            "settings": {
                "business_hours": {
                    "funday": {"open": "09:00", "close": "17:00"},
                },
            },
        },
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_valid_business_hours_persist(client: AsyncClient) -> None:
    owner_headers, org_id = await _org_with_owner(
        client,
        "ws-settings-good-hours@example.com",
    )
    workspace = await _create_workspace(client, org_id, owner_headers)

    response = await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={
            "settings": {
                "business_hours": {
                    "monday": {"open": "09:00", "close": "17:00"},
                    "sunday": None,
                },
            },
        },
        headers=owner_headers,
    )

    assert response.status_code == 200
    body = response.json()["settings"]["business_hours"]
    assert body["monday"] == {"open": "09:00", "close": "17:00"}
    assert body["sunday"] is None


async def test_setting_only_timezone_leaves_locale_and_hours_untouched(
    client: AsyncClient,
) -> None:
    owner_headers, org_id = await _org_with_owner(
        client,
        "ws-settings-partial@example.com",
    )
    workspace = await _create_workspace(client, org_id, owner_headers)

    await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={
            "settings": {
                "locale": "fr-CA",
                "business_hours": {"monday": {"open": "08:00", "close": "16:00"}},
            },
        },
        headers=owner_headers,
    )

    response = await client.patch(
        f"{_workspaces_url(org_id)}/{workspace['id']}",
        json={"settings": {"timezone": "Europe/Paris"}},
        headers=owner_headers,
    )

    assert response.status_code == 200
    body = response.json()["settings"]
    assert body["timezone"] == "Europe/Paris"
    assert body["locale"] == "fr-CA"
    assert body["business_hours"] == {"monday": {"open": "08:00", "close": "16:00"}}
