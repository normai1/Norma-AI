from httpx import AsyncClient

from tests.conftest import _org_with_owner, _signed_in

ORGS = "/api/v1/organizations"


async def _add_member(
    client: AsyncClient,
    organization_id: str,
    owner_headers: dict[str, str],
    email: str,
    role: str,
) -> dict[str, str]:
    headers = await _signed_in(client, email)

    invitation = await client.post(
        f"{ORGS}/{organization_id}/invitations",
        json={"email": email, "role": role},
        headers=owner_headers,
    )
    await client.post(
        "/api/v1/invitations/accept",
        json={"token": invitation.json()["token"]},
        headers=headers,
    )

    return headers


async def _org_with_member(
    client: AsyncClient,
    prefix: str,
    role: str,
) -> tuple[str, dict[str, str], dict[str, str]]:
    owner_headers, organization_id = await _org_with_owner(
        client,
        f"{prefix}-owner@example.com",
    )
    member_headers = await _add_member(
        client,
        organization_id,
        owner_headers,
        f"{prefix}-{role}@example.com",
        role,
    )

    return organization_id, owner_headers, member_headers


async def _create_workspace(
    client: AsyncClient,
    organization_id: str,
    owner_headers: dict[str, str],
    name: str,
) -> dict:
    response = await client.post(
        f"{ORGS}/{organization_id}/workspaces",
        json={"name": name},
        headers=owner_headers,
    )

    return response.json()


def _assistants_url(organization_id: str, workspace_id: str) -> str:
    return f"{ORGS}/{organization_id}/workspaces/{workspace_id}/assistants"


async def _create_assistant(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    name: str,
) -> dict:
    response = await client.post(
        _assistants_url(organization_id, workspace_id),
        json={"name": name},
        headers=headers,
    )

    return response.json()


def _versions_url(organization_id: str, workspace_id: str, assistant_id: str) -> str:
    return f"{_assistants_url(organization_id, workspace_id)}/{assistant_id}/versions"


_VALID_PAYLOAD = {
    "voice_id": "v1",
    "language": "en-US",
    "greeting": "Thanks for calling!",
    "persona": "Warm and efficient.",
    "speech_rate": 1.1,
    "turn_sensitivity": 0.6,
    "creativity": 0.4,
    "ambient_sound": None,
}


async def _setup_assistant(
    client: AsyncClient, prefix: str
) -> tuple[str, str, str, dict]:
    owner_headers, organization_id = await _org_with_owner(
        client, f"{prefix}@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )

    return organization_id, workspace["id"], created["id"], owner_headers


async def test_create_succeeds_and_assigns_version_one(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-create",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["voice_id"] == "v1"
    assert body["speech_rate"] == 1.1


async def test_second_create_assigns_version_two(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-second",
    )
    url = _versions_url(organization_id, workspace_id, assistant_id)

    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)
    second = await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    assert second.json()["version"] == 2


async def test_create_requires_authentication(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, _ = await _setup_assistant(
        client,
        "asstver-anon",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json=_VALID_PAYLOAD,
    )

    assert response.status_code == 401


async def test_create_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asstver-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )

    response = await client.post(
        _versions_url(organization_id, workspace["id"], created["id"]),
        json=_VALID_PAYLOAD,
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_create_rejects_out_of_bounds_speech_rate(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-badrate",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "speech_rate": 3.0},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_create_rejects_out_of_bounds_creativity(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-badcreativity",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "creativity": 1.5},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_create_rejects_out_of_bounds_turn_sensitivity(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-badsensitivity",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "turn_sensitivity": -0.1},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_list_returns_empty_for_an_assistant_with_no_versions(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-listempty",
    )

    response = await client.get(
        _versions_url(organization_id, workspace_id, assistant_id),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_and_get_are_reachable_by_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asstver-listmember",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    roster = await client.get(
        f"{ORGS}/{organization_id}/members", headers=owner_headers
    )
    member_id = next(
        m["id"]
        for m in roster.json()
        if m["user"]["email"] == "asstver-listmember-member@example.com"
    )
    await client.post(
        f"{ORGS}/{organization_id}/workspaces/{workspace['id']}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )
    url = _versions_url(organization_id, workspace["id"], created["id"])
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    list_response = await client.get(url, headers=member_headers)
    get_response = await client.get(f"{url}/1", headers=member_headers)

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert get_response.status_code == 200
    assert get_response.json()["version"] == 1


async def test_get_is_not_found_for_a_nonexistent_version(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-missing",
    )

    response = await client.get(
        f"{_versions_url(organization_id, workspace_id, assistant_id)}/99",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_version_in_one_assistant_is_not_reachable_through_a_sibling_assistant(
    client: AsyncClient,
) -> None:
    (
        organization_id,
        workspace_id,
        assistant_a_id,
        owner_headers,
    ) = await _setup_assistant(
        client,
        "asstver-sibling",
    )
    assistant_b = await _create_assistant(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        "Second Desk",
    )
    await client.post(
        _versions_url(organization_id, workspace_id, assistant_a_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    response = await client.get(
        f"{_versions_url(organization_id, workspace_id, assistant_b['id'])}/1",
        headers=owner_headers,
    )

    assert response.status_code == 404
