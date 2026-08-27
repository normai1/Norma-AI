from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace_member import WorkspaceMember
from tests.conftest import _org_with_owner, _signed_in

ORGS = "/api/v1/organizations"


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


async def test_create_succeeds_for_an_admin(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-create-owner@example.com",
    )

    response = await client.post(
        _workspaces_url(organization_id),
        json={"name": "Downtown Clinic"},
        headers=owner_headers,
    )

    assert response.status_code == 201

    body = response.json()

    assert body["name"] == "Downtown Clinic"
    assert body["organization_id"] == organization_id
    assert body["settings"] == {}


async def test_create_is_denied_for_a_member(client: AsyncClient) -> None:
    organization_id, _, member_headers = await _org_with_member(
        client,
        "ws-create-member",
        "member",
    )

    response = await client.post(
        _workspaces_url(organization_id),
        json={"name": "Should Not Exist"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_create_is_denied_for_a_viewer(client: AsyncClient) -> None:
    organization_id, _, viewer_headers = await _org_with_member(
        client,
        "ws-create-viewer",
        "viewer",
    )

    response = await client.post(
        _workspaces_url(organization_id),
        json={"name": "Should Not Exist"},
        headers=viewer_headers,
    )

    assert response.status_code == 403


async def test_create_rejects_an_empty_name(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-create-empty@example.com",
    )

    response = await client.post(
        _workspaces_url(organization_id),
        json={"name": ""},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_list_returns_every_workspace_for_an_admin(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-list-admin@example.com",
    )

    await _create_workspace(client, organization_id, owner_headers, "A")
    await _create_workspace(client, organization_id, owner_headers, "B")

    response = await client.get(_workspaces_url(organization_id), headers=owner_headers)

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_list_is_empty_for_a_member_with_no_memberships(
    client: AsyncClient,
) -> None:
    organization_id, _, member_headers = await _org_with_member(
        client,
        "ws-list-member",
        "member",
    )

    response = await client.get(
        _workspaces_url(organization_id),
        headers=member_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_is_rejected_for_a_non_member(client: AsyncClient) -> None:
    _, organization_id = await _org_with_owner(client, "ws-list-owner@example.com")
    outsider = await _signed_in(client, "ws-list-outsider@example.com")

    response = await client.get(_workspaces_url(organization_id), headers=outsider)

    assert response.status_code == 404


async def test_workspace_routes_require_authentication(client: AsyncClient) -> None:
    _, organization_id = await _org_with_owner(client, "ws-auth-owner@example.com")

    create = await client.post(
        _workspaces_url(organization_id),
        json={"name": "No Token"},
    )
    listing = await client.get(_workspaces_url(organization_id))

    assert create.status_code == 401
    assert listing.status_code == 401


async def test_get_succeeds_for_an_explicit_member(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """
    Proves the WorkspaceMember access path works before 6b ships any endpoint
    that can grant it - the row is inserted directly, the same way
    test_organization_concurrency.py tests mechanisms ahead of their API.
    """

    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "ws-get-member",
        "member",
    )
    created = await _create_workspace(
        client,
        organization_id,
        owner_headers,
        "Member Reachable",
    )

    member = await db.scalar(
        select(User).where(User.email == "ws-get-member-member@example.com"),
    )
    db.add(WorkspaceMember(workspace_id=created["id"], user_id=member.id))
    await db.flush()
    await db.commit()

    response = await client.get(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        headers=member_headers,
    )

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_succeeds_for_an_admin_without_explicit_membership(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-get-admin@example.com",
    )
    created = await _create_workspace(
        client,
        organization_id,
        owner_headers,
        "Admin Reachable",
    )

    response = await client.get(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        headers=owner_headers,
    )

    assert response.status_code == 200


async def test_get_404s_for_a_member_with_no_access(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "ws-get-outsider",
        "member",
    )
    created = await _create_workspace(
        client,
        organization_id,
        owner_headers,
        "Not Reachable",
    )

    response = await client.get(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        headers=member_headers,
    )

    assert response.status_code == 404


async def test_update_succeeds_for_an_admin(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-update-admin@example.com",
    )
    created = await _create_workspace(
        client,
        organization_id,
        owner_headers,
        "Original Name",
    )

    response = await client.patch(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        json={"name": "Renamed"},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


async def test_update_settings_without_touching_name(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-update-settings@example.com",
    )
    created = await _create_workspace(
        client,
        organization_id,
        owner_headers,
        "Keep My Name",
    )

    response = await client.patch(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        json={"settings": {"timezone": "Asia/Kolkata"}},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["settings"] == {"timezone": "Asia/Kolkata"}
    assert response.json()["name"] == "Keep My Name"


async def test_update_name_without_touching_settings(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-update-name-only@example.com",
    )
    created = await _create_workspace(
        client,
        organization_id,
        owner_headers,
        "Original Name",
    )

    await client.patch(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        json={"settings": {"timezone": "Asia/Kolkata"}},
        headers=owner_headers,
    )

    response = await client.patch(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        json={"name": "Renamed Only"},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Only"
    assert response.json()["settings"] == {"timezone": "Asia/Kolkata"}


async def test_update_is_denied_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "ws-update-member",
        "member",
    )
    created = await _create_workspace(
        client,
        organization_id,
        owner_headers,
        "Untouchable",
    )

    response = await client.patch(
        f"{_workspaces_url(organization_id)}/{created['id']}",
        json={"name": "Hijacked"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_update_404s_for_a_workspace_in_another_organization(
    client: AsyncClient,
) -> None:
    owner_a_headers, organization_a_id = await _org_with_owner(
        client,
        "ws-cross-a@example.com",
    )
    owner_b_headers, organization_b_id = await _org_with_owner(
        client,
        "ws-cross-b@example.com",
    )
    created = await _create_workspace(
        client,
        organization_b_id,
        owner_b_headers,
        "Org B Workspace",
    )

    response = await client.patch(
        f"{_workspaces_url(organization_a_id)}/{created['id']}",
        json={"name": "Stolen"},
        headers=owner_a_headers,
    )

    assert response.status_code == 404


async def test_get_rejects_a_malformed_workspace_id(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "ws-malformed@example.com",
    )

    response = await client.get(
        f"{_workspaces_url(organization_id)}/not-a-uuid",
        headers=owner_headers,
    )

    assert response.status_code == 422
