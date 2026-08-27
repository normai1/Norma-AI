"""
Cross-organization tenant-isolation regression suite.

Every route below is scoped by an `organization_id` path parameter. A caller who
does not belong to that organization must get the same 404 `require_org_member`
already returns for a nonexistent id - never data, never a distinguishable error.
Existing suites prove role-based denial *within* one organization
(test_organization_authorization.py, test_permission_enforcement.py); nothing
before this proved denial *across* organizations as its own concern.
"""

import uuid

from httpx import AsyncClient

from tests.conftest import _org_with_owner, _signed_in

ORGS = "/api/v1/organizations"


def _routes_for(organization_id: str) -> list[tuple[str, str, dict | None]]:
    """
    Every organization_id-scoped route, with syntactically valid but unresolved
    ids for the member/invitation segments - the membership check must reject
    the caller before any of those ids are ever looked up.
    """

    member_id = uuid.uuid4()
    invitation_id = uuid.uuid4()

    return [
        ("GET", f"{ORGS}/{organization_id}", None),
        ("PATCH", f"{ORGS}/{organization_id}", {"name": "Hijacked"}),
        ("GET", f"{ORGS}/{organization_id}/members", None),
        (
            "PATCH",
            f"{ORGS}/{organization_id}/members/{member_id}",
            {"role": "viewer"},
        ),
        ("DELETE", f"{ORGS}/{organization_id}/members/{member_id}", None),
        (
            "POST",
            f"{ORGS}/{organization_id}/invitations",
            {"email": "outsider-probe@example.com"},
        ),
        ("GET", f"{ORGS}/{organization_id}/invitations", None),
        ("DELETE", f"{ORGS}/{organization_id}/invitations/{invitation_id}", None),
    ]


async def test_member_of_another_organization_is_denied_every_route(
    client: AsyncClient,
) -> None:
    org_a_headers, _ = await _org_with_owner(client, "isolation-a-owner@example.com")
    _, org_b_id = await _org_with_owner(client, "isolation-b-owner@example.com")

    for method, path, payload in _routes_for(org_b_id):
        response = await client.request(
            method,
            path,
            headers=org_a_headers,
            json=payload,
        )

        assert response.status_code == 404, f"{method} {path}"


async def test_user_with_no_organization_is_denied_every_route(
    client: AsyncClient,
) -> None:
    outsider = await _signed_in(client, "isolation-no-org@example.com")
    _, org_id = await _org_with_owner(client, "isolation-target-owner@example.com")

    for method, path, payload in _routes_for(org_id):
        response = await client.request(method, path, headers=outsider, json=payload)

        assert response.status_code == 404, f"{method} {path}"


async def test_list_organizations_never_includes_a_foreign_organization(
    client: AsyncClient,
) -> None:
    org_a_headers, org_a_id = await _org_with_owner(
        client,
        "isolation-list-a@example.com",
    )
    _, org_b_id = await _org_with_owner(client, "isolation-list-b@example.com")

    response = await client.get(ORGS, headers=org_a_headers)

    visible_ids = {organization["id"] for organization in response.json()}

    assert org_a_id in visible_ids
    assert org_b_id not in visible_ids
