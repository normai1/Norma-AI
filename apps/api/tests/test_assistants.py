from httpx import AsyncClient
from norma_shared.voice_session_ticket import decode_voice_session_ticket

from app.core.config import settings
from tests.conftest import _org_with_owner, _signed_in

ORGS = "/api/v1/organizations"
_MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _workspaces_url(organization_id: str) -> str:
    return f"{ORGS}/{organization_id}/workspaces"


async def _add_member(
    client: AsyncClient,
    organization_id: str,
    owner_headers: dict[str, str],
    email: str,
    role: str,
) -> dict[str, str]:
    """
    Invite and accept a new member into an existing organization.
    """

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
    """
    Create an organization and add a second user at the given role.

    Returns the organization id, the owner's headers, and the new member's.
    """

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


def _assistants_url(organization_id: str, workspace_id: str) -> str:
    return f"{_workspaces_url(organization_id)}/{workspace_id}/assistants"


async def _create_workspace(
    client: AsyncClient,
    organization_id: str,
    owner_headers: dict[str, str],
    name: str,
) -> dict:
    response = await client.post(
        _workspaces_url(organization_id),
        json={"name": name},
        headers=owner_headers,
    )

    return response.json()


async def _org_member_id(
    client: AsyncClient,
    organization_id: str,
    owner_headers: dict[str, str],
    email: str,
) -> str:
    roster = await client.get(
        f"{ORGS}/{organization_id}/members", headers=owner_headers
    )

    return next(m["id"] for m in roster.json() if m["user"]["email"] == email)


async def _grant_workspace_access(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    owner_headers: dict[str, str],
    member_id: str,
) -> None:
    await client.post(
        f"{_workspaces_url(organization_id)}/{workspace_id}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )


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


_VALID_VERSION_PAYLOAD = {
    "voice_id": "v1",
    "language": "en-US",
    "greeting": "Thanks for calling!",
    "persona": "Warm and efficient.",
    "speech_rate": 1.1,
    "turn_sensitivity": 0.6,
    "creativity": 0.4,
    "ambient_sound": None,
}


async def _create_version(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    assistant_id: str,
    headers: dict[str, str],
) -> dict:
    response = await client.post(
        f"{_assistants_url(organization_id, workspace_id)}/{assistant_id}/versions",
        json=_VALID_VERSION_PAYLOAD,
        headers=headers,
    )

    return response.json()


async def test_create_succeeds_for_an_owner(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-create-owner@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        _assistants_url(organization_id, workspace["id"]),
        json={"name": "Front Desk"},
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Front Desk"
    assert body["status"] == "draft"
    assert body["organization_id"] == organization_id
    assert body["workspace_id"] == workspace["id"]
    assert body["current_version_id"] is None


async def test_create_requires_authentication(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-create-anon@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        _assistants_url(organization_id, workspace["id"]),
        json={"name": "Front Desk"},
    )

    assert response.status_code == 401


async def test_create_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asst-create-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        _assistants_url(organization_id, workspace["id"]),
        json={"name": "Front Desk"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_list_returns_empty_for_a_new_workspace(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-list-empty@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.get(
        _assistants_url(organization_id, workspace["id"]),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_and_get_are_reachable_by_an_explicit_workspace_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asst-list-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    member_id = await _org_member_id(
        client,
        organization_id,
        owner_headers,
        "asst-list-member-member@example.com",
    )
    await _grant_workspace_access(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        member_id,
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )

    list_response = await client.get(
        _assistants_url(organization_id, workspace["id"]),
        headers=member_headers,
    )
    get_response = await client.get(
        f"{_assistants_url(organization_id, workspace['id'])}/{created['id']}",
        headers=member_headers,
    )

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]


async def test_get_is_not_found_for_a_nonexistent_assistant(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-get-missing@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.get(
        f"{_assistants_url(organization_id, workspace['id'])}/{_MISSING_ID}",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_rename_succeeds_for_an_owner(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-rename-owner@example.com",
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

    response = await client.patch(
        f"{_assistants_url(organization_id, workspace['id'])}/{created['id']}",
        json={"name": "Renamed"},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


async def test_rename_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asst-rename-member",
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

    response = await client.patch(
        f"{_assistants_url(organization_id, workspace['id'])}/{created['id']}",
        json={"name": "Renamed"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_rename_is_not_found_for_a_nonexistent_assistant(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-rename-missing@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.patch(
        f"{_assistants_url(organization_id, workspace['id'])}/{_MISSING_ID}",
        json={"name": "Renamed"},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_archive_succeeds_and_is_idempotent(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-archive-owner@example.com",
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
    archive_url = (
        f"{_assistants_url(organization_id, workspace['id'])}/{created['id']}/archive"
    )

    first = await client.post(archive_url, headers=owner_headers)
    second = await client.post(archive_url, headers=owner_headers)

    assert first.status_code == 200
    assert first.json()["status"] == "archived"
    assert second.status_code == 200
    assert second.json()["status"] == "archived"


async def test_archive_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asst-archive-member",
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
        f"{_assistants_url(organization_id, workspace['id'])}/{created['id']}/archive",
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_assistant_in_one_workspace_is_not_reachable_through_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-sibling-workspace@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_id, owner_headers, "Clinic A"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace_a["id"],
        owner_headers,
        "Front Desk",
    )

    response = await client.get(
        f"{_assistants_url(organization_id, workspace_b['id'])}/{created['id']}",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_assistant_in_one_organization_is_not_reachable_through_another(
    client: AsyncClient,
) -> None:
    owner_a_headers, organization_a_id = await _org_with_owner(
        client,
        "asst-sibling-org-a@example.com",
    )
    owner_b_headers, organization_b_id = await _org_with_owner(
        client,
        "asst-sibling-org-b@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_a_id, owner_a_headers, "Clinic A"
    )
    created = await _create_assistant(
        client,
        organization_a_id,
        workspace_a["id"],
        owner_a_headers,
        "Front Desk",
    )

    response = await client.get(
        f"{_assistants_url(organization_b_id, workspace_a['id'])}/{created['id']}",
        headers=owner_b_headers,
    )

    assert response.status_code == 404


def _publish_url(organization_id: str, workspace_id: str, assistant_id: str) -> str:
    return f"{_assistants_url(organization_id, workspace_id)}/{assistant_id}/publish"


async def test_publish_succeeds_and_flips_status(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-publish-owner@example.com",
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
    version = await _create_version(
        client,
        organization_id,
        workspace["id"],
        created["id"],
        owner_headers,
    )

    response = await client.post(
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": version["version"]},
        headers=owner_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "published"
    assert body["current_version_id"] == version["id"]


async def test_publish_the_same_version_twice_is_idempotent(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-publish-idempotent@example.com",
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
    version = await _create_version(
        client,
        organization_id,
        workspace["id"],
        created["id"],
        owner_headers,
    )
    url = _publish_url(organization_id, workspace["id"], created["id"])

    first = await client.post(
        url, json={"version": version["version"]}, headers=owner_headers
    )
    second = await client.post(
        url, json={"version": version["version"]}, headers=owner_headers
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "published"


async def test_publish_can_roll_back_to_an_older_version(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-publish-rollback@example.com",
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
    version_one = await _create_version(
        client,
        organization_id,
        workspace["id"],
        created["id"],
        owner_headers,
    )
    version_two = await _create_version(
        client,
        organization_id,
        workspace["id"],
        created["id"],
        owner_headers,
    )
    url = _publish_url(organization_id, workspace["id"], created["id"])

    await client.post(
        url, json={"version": version_two["version"]}, headers=owner_headers
    )
    rollback = await client.post(
        url,
        json={"version": version_one["version"]},
        headers=owner_headers,
    )

    assert rollback.status_code == 200
    assert rollback.json()["current_version_id"] == version_one["id"]


async def test_publish_requires_authentication(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-publish-anon@example.com",
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
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": 1},
    )

    assert response.status_code == 401


async def test_publish_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asst-publish-member",
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
    version = await _create_version(
        client,
        organization_id,
        workspace["id"],
        created["id"],
        owner_headers,
    )

    response = await client.post(
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": version["version"]},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_publish_rejects_a_nonexistent_version(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-publish-badversion@example.com",
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
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": 99},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_publish_rejects_an_archived_assistant(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-publish-archived@example.com",
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
    version = await _create_version(
        client,
        organization_id,
        workspace["id"],
        created["id"],
        owner_headers,
    )
    await client.post(
        f"{_assistants_url(organization_id, workspace['id'])}/{created['id']}/archive",
        headers=owner_headers,
    )

    response = await client.post(
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": version["version"]},
        headers=owner_headers,
    )

    assert response.status_code == 409


def _test_call_token_url(
    organization_id: str, workspace_id: str, assistant_id: str
) -> str:
    base = _assistants_url(organization_id, workspace_id)
    return f"{base}/{assistant_id}/test-call-token"


async def test_test_call_token_succeeds_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asst-ticket-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    member_id = await _org_member_id(
        client,
        organization_id,
        owner_headers,
        "asst-ticket-member-member@example.com",
    )
    await _grant_workspace_access(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        member_id,
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )

    response = await client.post(
        _test_call_token_url(organization_id, workspace["id"], created["id"]),
        headers=member_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] > 0
    assistant_id = decode_voice_session_ticket(
        body["ticket"],
        secret_key=settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    assert assistant_id == created["id"]


async def test_test_call_token_requires_authentication(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-ticket-anon@example.com",
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
        _test_call_token_url(organization_id, workspace["id"], created["id"]),
    )

    assert response.status_code == 401


async def test_test_call_token_is_not_found_for_a_nonexistent_assistant(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-ticket-missing@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        _test_call_token_url(organization_id, workspace["id"], _MISSING_ID),
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_test_call_token_sibling_workspace_is_not_reachable(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-ticket-sibling@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_id, owner_headers, "Clinic A"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace_a["id"],
        owner_headers,
        "Front Desk",
    )

    response = await client.post(
        _test_call_token_url(organization_id, workspace_b["id"], created["id"]),
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_publish_in_one_workspace_is_not_reachable_through_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asst-publish-sibling@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_id, owner_headers, "Clinic A"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace_a["id"],
        owner_headers,
        "Front Desk",
    )
    version = await _create_version(
        client,
        organization_id,
        workspace_a["id"],
        created["id"],
        owner_headers,
    )

    response = await client.post(
        _publish_url(organization_id, workspace_b["id"], created["id"]),
        json={"version": version["version"]},
        headers=owner_headers,
    )

    assert response.status_code == 404
