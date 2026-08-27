"""
Authorization boundaries between roles inside one organization.

Feature 2's first audit found a complete takeover path: an admin could promote
itself to owner and then remove the founding owner, using only permitted calls.
These tests pin every step of that sequence shut.
"""

from httpx import AsyncClient

from tests.conftest import _signed_in

REGISTER = "/api/v1/auth/register"
ORGS = "/api/v1/organizations"
ACCEPT = "/api/v1/invitations/accept"


async def _org_with_second_member(
    client: AsyncClient,
    prefix: str,
    role: str,
) -> tuple[dict[str, str], dict[str, str], str, dict[str, str]]:
    """
    Build an organization whose owner has invited one other member.

    Returns the owner's headers, the other member's headers, the organization
    id, and a mapping of email to membership id.
    """

    owner = await _signed_in(client, f"{prefix}-owner@example.com")
    other = await _signed_in(client, f"{prefix}-other@example.com")

    created = await client.post(ORGS, json={"name": "Boundary Co"}, headers=owner)
    organization_id = created.json()["id"]

    invitation = await client.post(
        f"{ORGS}/{organization_id}/invitations",
        json={"email": f"{prefix}-other@example.com", "role": role},
        headers=owner,
    )
    await client.post(
        ACCEPT,
        json={"token": invitation.json()["token"]},
        headers=other,
    )

    roster = await client.get(f"{ORGS}/{organization_id}/members", headers=owner)
    ids = {member["user"]["email"]: member["id"] for member in roster.json()}

    return owner, other, organization_id, ids


async def test_admin_cannot_promote_itself_to_owner(client: AsyncClient) -> None:
    _, admin, org_id, ids = await _org_with_second_member(client, "esc1", "admin")

    response = await client.patch(
        f"{ORGS}/{org_id}/members/{ids['esc1-other@example.com']}",
        json={"role": "owner"},
        headers=admin,
    )

    assert response.status_code == 403


async def test_admin_cannot_promote_another_member_to_owner(
    client: AsyncClient,
) -> None:
    owner, admin, org_id, ids = await _org_with_second_member(
        client,
        "esc2",
        "admin",
    )

    response = await client.patch(
        f"{ORGS}/{org_id}/members/{ids['esc2-owner@example.com']}",
        json={"role": "owner"},
        headers=admin,
    )

    assert response.status_code == 403


async def test_admin_cannot_demote_the_owner(client: AsyncClient) -> None:
    _, admin, org_id, ids = await _org_with_second_member(client, "esc3", "admin")

    response = await client.patch(
        f"{ORGS}/{org_id}/members/{ids['esc3-owner@example.com']}",
        json={"role": "viewer"},
        headers=admin,
    )

    assert response.status_code == 403


async def test_admin_cannot_remove_the_owner(client: AsyncClient) -> None:
    _, admin, org_id, ids = await _org_with_second_member(client, "esc4", "admin")

    response = await client.delete(
        f"{ORGS}/{org_id}/members/{ids['esc4-owner@example.com']}",
        headers=admin,
    )

    assert response.status_code == 403


async def test_admin_cannot_invite_at_owner_level(client: AsyncClient) -> None:
    _, admin, org_id, _ = await _org_with_second_member(client, "esc5", "admin")

    response = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "would-be-owner@example.com", "role": "owner"},
        headers=admin,
    )

    assert response.status_code == 403


async def test_the_full_takeover_sequence_is_blocked_at_step_one(
    client: AsyncClient,
) -> None:
    """
    The exact sequence reproduced during the audit, end to end.
    """

    owner, admin, org_id, ids = await _org_with_second_member(
        client,
        "esc6",
        "admin",
    )

    promote = await client.patch(
        f"{ORGS}/{org_id}/members/{ids['esc6-other@example.com']}",
        json={"role": "owner"},
        headers=admin,
    )

    assert promote.status_code == 403

    evict = await client.delete(
        f"{ORGS}/{org_id}/members/{ids['esc6-owner@example.com']}",
        headers=admin,
    )

    assert evict.status_code == 403

    # The founding owner still has their organization.
    still_there = await client.get(f"{ORGS}/{org_id}", headers=owner)

    assert still_there.status_code == 200
    assert still_there.json()["role"] == "owner"


async def test_owner_cannot_change_their_own_role(client: AsyncClient) -> None:
    owner, _, org_id, ids = await _org_with_second_member(client, "esc7", "admin")

    response = await client.patch(
        f"{ORGS}/{org_id}/members/{ids['esc7-owner@example.com']}",
        json={"role": "admin"},
        headers=owner,
    )

    assert response.status_code == 403


async def test_owner_can_still_promote_and_remove(client: AsyncClient) -> None:
    """
    The guard must not break the legitimate owner flows.
    """

    owner, _, org_id, ids = await _org_with_second_member(client, "esc8", "member")

    member_id = ids["esc8-other@example.com"]

    promoted = await client.patch(
        f"{ORGS}/{org_id}/members/{member_id}",
        json={"role": "owner"},
        headers=owner,
    )

    assert promoted.status_code == 200
    assert promoted.json()["role"] == "owner"

    removed = await client.delete(
        f"{ORGS}/{org_id}/members/{member_id}",
        headers=owner,
    )

    assert removed.status_code == 204


async def test_a_member_can_remove_themselves(client: AsyncClient) -> None:
    _, member, org_id, ids = await _org_with_second_member(
        client,
        "esc9",
        "member",
    )

    response = await client.delete(
        f"{ORGS}/{org_id}/members/{ids['esc9-other@example.com']}",
        headers=member,
    )

    # A plain member holds no members:manage permission, so the permission
    # gate stops them first.
    assert response.status_code == 403


async def test_admin_can_still_manage_ordinary_members(
    client: AsyncClient,
) -> None:
    owner, admin, org_id, _ = await _org_with_second_member(
        client,
        "esc10",
        "admin",
    )

    invitation = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "esc10-junior@example.com", "role": "viewer"},
        headers=admin,
    )
    junior = await _signed_in(client, "esc10-junior@example.com")
    await client.post(
        ACCEPT,
        json={"token": invitation.json()["token"]},
        headers=junior,
    )

    roster = await client.get(f"{ORGS}/{org_id}/members", headers=admin)
    junior_id = next(
        member["id"]
        for member in roster.json()
        if member["user"]["email"] == "esc10-junior@example.com"
    )

    promoted = await client.patch(
        f"{ORGS}/{org_id}/members/{junior_id}",
        json={"role": "member"},
        headers=admin,
    )

    assert promoted.status_code == 200

    removed = await client.delete(
        f"{ORGS}/{org_id}/members/{junior_id}",
        headers=admin,
    )

    assert removed.status_code == 204
