"""
Proof that non-elevated roles are denied at every permission-gated route.

Every test here previously had no coverage at all: prior suites proved
owner/admin succeed and proved owner-vs-admin escalation is blocked, but
nothing ever asserted the baseline case - that a plain member or a viewer is
turned away in the first place. Found while scoping this feature.
"""

import uuid

from httpx import AsyncClient

from tests.conftest import _signed_in

REGISTER = "/api/v1/auth/register"
ORGS = "/api/v1/organizations"

ROLES_WITHOUT_PERMISSIONS = ("member", "viewer")


async def _org_with_role(
    client: AsyncClient,
    prefix: str,
    role: str,
) -> tuple[str, dict[str, str]]:
    """
    Create an organization and add a second member holding the given role.

    Returns the organization id and that member's auth headers.
    """

    owner = await _signed_in(client, f"{prefix}-owner@example.com")
    other = await _signed_in(client, f"{prefix}-{role}@example.com")

    created = await client.post(ORGS, json={"name": "Gated Co"}, headers=owner)
    organization_id = created.json()["id"]

    invitation = await client.post(
        f"{ORGS}/{organization_id}/invitations",
        json={"email": f"{prefix}-{role}@example.com", "role": role},
        headers=owner,
    )
    await client.post(
        "/api/v1/invitations/accept",
        json={"token": invitation.json()["token"]},
        headers=other,
    )

    return organization_id, other


async def test_update_organization_denies_non_elevated_roles(
    client: AsyncClient,
) -> None:
    for role in ROLES_WITHOUT_PERMISSIONS:
        org_id, headers = await _org_with_role(client, f"upd-{role}", role)

        response = await client.patch(
            f"{ORGS}/{org_id}",
            json={"name": "Hijacked"},
            headers=headers,
        )

        assert response.status_code == 403, role


async def test_change_member_role_denies_non_elevated_roles(
    client: AsyncClient,
) -> None:
    for role in ROLES_WITHOUT_PERMISSIONS:
        org_id, headers = await _org_with_role(client, f"chg-{role}", role)

        roster = await client.get(f"{ORGS}/{org_id}/members", headers=headers)
        member_id = next(
            m["id"]
            for m in roster.json()
            if m["user"]["email"] == f"chg-{role}-{role}@example.com"
        )

        response = await client.patch(
            f"{ORGS}/{org_id}/members/{member_id}",
            json={"role": "viewer"},
            headers=headers,
        )

        assert response.status_code == 403, role


async def test_remove_member_denies_non_elevated_roles(
    client: AsyncClient,
) -> None:
    for role in ROLES_WITHOUT_PERMISSIONS:
        org_id, headers = await _org_with_role(client, f"rm-{role}", role)

        roster = await client.get(f"{ORGS}/{org_id}/members", headers=headers)
        member_id = next(
            m["id"]
            for m in roster.json()
            if m["user"]["email"] == f"rm-{role}-{role}@example.com"
        )

        response = await client.delete(
            f"{ORGS}/{org_id}/members/{member_id}",
            headers=headers,
        )

        assert response.status_code == 403, role


async def test_create_invitation_denies_non_elevated_roles(
    client: AsyncClient,
) -> None:
    for role in ROLES_WITHOUT_PERMISSIONS:
        org_id, headers = await _org_with_role(client, f"inv-{role}", role)

        response = await client.post(
            f"{ORGS}/{org_id}/invitations",
            json={"email": "someone-new@example.com"},
            headers=headers,
        )

        assert response.status_code == 403, role


async def test_revoke_invitation_denies_non_elevated_roles(
    client: AsyncClient,
) -> None:
    for role in ROLES_WITHOUT_PERMISSIONS:
        org_id, headers = await _org_with_role(client, f"rev-{role}", role)

        response = await client.delete(
            f"{ORGS}/{org_id}/invitations/{uuid.uuid4()}",
            headers=headers,
        )

        assert response.status_code == 403, role


async def test_read_only_routes_remain_open_to_every_role(
    client: AsyncClient,
) -> None:
    """
    The permission refactor must not narrow access that was never gated.
    """

    for role in ROLES_WITHOUT_PERMISSIONS:
        org_id, headers = await _org_with_role(client, f"read-{role}", role)

        assert (
            await client.get(f"{ORGS}/{org_id}", headers=headers)
        ).status_code == 200
        assert (
            await client.get(f"{ORGS}/{org_id}/members", headers=headers)
        ).status_code == 200
        assert (
            await client.get(f"{ORGS}/{org_id}/invitations", headers=headers)
        ).status_code == 200
