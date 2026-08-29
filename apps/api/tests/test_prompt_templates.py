from httpx import AsyncClient

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


def _prompt_templates_url(organization_id: str, workspace_id: str) -> str:
    return f"{_workspaces_url(organization_id)}/{workspace_id}/prompt-templates"


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


async def _create_prompt_template(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    name: str,
    use_case: str = "receptionist",
) -> dict:
    response = await client.post(
        _prompt_templates_url(organization_id, workspace_id),
        json={"name": name, "use_case": use_case},
        headers=headers,
    )

    return response.json()


_VALID_VERSION_PAYLOAD = {"content": "Thanks for calling {{workspace.name}}!"}


async def _create_version(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    prompt_template_id: str,
    headers: dict[str, str],
) -> dict:
    base_url = _prompt_templates_url(organization_id, workspace_id)
    response = await client.post(
        f"{base_url}/{prompt_template_id}/versions",
        json=_VALID_VERSION_PAYLOAD,
        headers=headers,
    )

    return response.json()


async def test_create_succeeds_for_an_owner(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-create-owner@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        _prompt_templates_url(organization_id, workspace["id"]),
        json={"name": "Front Desk Receptionist", "use_case": "receptionist"},
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Front Desk Receptionist"
    assert body["use_case"] == "receptionist"
    assert body["status"] == "draft"
    assert body["organization_id"] == organization_id
    assert body["workspace_id"] == workspace["id"]
    assert body["current_version_id"] is None


async def test_create_requires_authentication(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-create-anon@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        _prompt_templates_url(organization_id, workspace["id"]),
        json={"name": "Front Desk Receptionist", "use_case": "receptionist"},
    )

    assert response.status_code == 401


async def test_create_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "pt-create-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        _prompt_templates_url(organization_id, workspace["id"]),
        json={"name": "Front Desk Receptionist", "use_case": "receptionist"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_list_returns_empty_for_a_new_workspace(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-list-empty@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.get(
        _prompt_templates_url(organization_id, workspace["id"]),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_and_get_are_reachable_by_an_explicit_workspace_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "pt-list-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    member_id = await _org_member_id(
        client,
        organization_id,
        owner_headers,
        "pt-list-member-member@example.com",
    )
    await _grant_workspace_access(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        member_id,
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )

    list_response = await client.get(
        _prompt_templates_url(organization_id, workspace["id"]),
        headers=member_headers,
    )
    get_response = await client.get(
        f"{_prompt_templates_url(organization_id, workspace['id'])}/{created['id']}",
        headers=member_headers,
    )

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]


async def test_get_is_not_found_for_a_nonexistent_prompt_template(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-get-missing@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.get(
        f"{_prompt_templates_url(organization_id, workspace['id'])}/{_MISSING_ID}",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_rename_succeeds_for_an_owner(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-rename-owner@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )

    response = await client.patch(
        f"{_prompt_templates_url(organization_id, workspace['id'])}/{created['id']}",
        json={"name": "Renamed"},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


async def test_rename_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "pt-rename-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )

    response = await client.patch(
        f"{_prompt_templates_url(organization_id, workspace['id'])}/{created['id']}",
        json={"name": "Renamed"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_rename_is_not_found_for_a_nonexistent_prompt_template(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-rename-missing@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.patch(
        f"{_prompt_templates_url(organization_id, workspace['id'])}/{_MISSING_ID}",
        json={"name": "Renamed"},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_archive_succeeds_and_is_idempotent(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-archive-owner@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )
    base_url = _prompt_templates_url(organization_id, workspace["id"])
    archive_url = f"{base_url}/{created['id']}/archive"

    first = await client.post(archive_url, headers=owner_headers)
    second = await client.post(archive_url, headers=owner_headers)

    assert first.status_code == 200
    assert first.json()["status"] == "archived"
    assert second.status_code == 200
    assert second.json()["status"] == "archived"


async def test_archive_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "pt-archive-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )

    base_url = _prompt_templates_url(organization_id, workspace["id"])
    response = await client.post(
        f"{base_url}/{created['id']}/archive",
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_prompt_template_cannot_be_reached_via_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-sibling-workspace@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_id, owner_headers, "Clinic A"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace_a["id"],
        owner_headers,
        "Front Desk Receptionist",
    )

    response = await client.get(
        f"{_prompt_templates_url(organization_id, workspace_b['id'])}/{created['id']}",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_prompt_template_in_one_organization_is_not_reachable_through_another(
    client: AsyncClient,
) -> None:
    owner_a_headers, organization_a_id = await _org_with_owner(
        client,
        "pt-sibling-org-a@example.com",
    )
    owner_b_headers, organization_b_id = await _org_with_owner(
        client,
        "pt-sibling-org-b@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_a_id, owner_a_headers, "Clinic A"
    )
    created = await _create_prompt_template(
        client,
        organization_a_id,
        workspace_a["id"],
        owner_a_headers,
        "Front Desk Receptionist",
    )

    base_url = _prompt_templates_url(organization_b_id, workspace_a["id"])
    response = await client.get(
        f"{base_url}/{created['id']}",
        headers=owner_b_headers,
    )

    assert response.status_code == 404


def _publish_url(
    organization_id: str, workspace_id: str, prompt_template_id: str
) -> str:
    base_url = _prompt_templates_url(organization_id, workspace_id)

    return f"{base_url}/{prompt_template_id}/publish"


async def test_publish_succeeds_and_flips_status(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-publish-owner@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
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
        "pt-publish-idempotent@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
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
        "pt-publish-rollback@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
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
        "pt-publish-anon@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )

    response = await client.post(
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": 1},
    )

    assert response.status_code == 401


async def test_publish_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "pt-publish-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
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
        "pt-publish-badversion@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )

    response = await client.post(
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": 99},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_publish_rejects_an_archived_prompt_template(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-publish-archived@example.com",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )
    version = await _create_version(
        client,
        organization_id,
        workspace["id"],
        created["id"],
        owner_headers,
    )
    base_url = _prompt_templates_url(organization_id, workspace["id"])
    await client.post(
        f"{base_url}/{created['id']}/archive",
        headers=owner_headers,
    )

    response = await client.post(
        _publish_url(organization_id, workspace["id"], created["id"]),
        json={"version": version["version"]},
        headers=owner_headers,
    )

    assert response.status_code == 409


async def test_publish_in_one_workspace_is_not_reachable_through_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "pt-publish-sibling@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_id, owner_headers, "Clinic A"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace_a["id"],
        owner_headers,
        "Front Desk Receptionist",
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
