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
        client, f"{prefix}-owner@example.com"
    )
    member_headers = await _add_member(
        client, organization_id, owner_headers, f"{prefix}-{role}@example.com", role
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


def _knowledge_sources_url(organization_id: str, workspace_id: str) -> str:
    return f"{_workspaces_url(organization_id)}/{workspace_id}/knowledge-sources"


async def _create_manual_faq_source(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    name: str = "General FAQ",
):
    return await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/manual-faq",
        json={"name": name},
        headers=headers,
    )


def _faq_entries_url(
    organization_id: str, workspace_id: str, knowledge_source_id: str
) -> str:
    base = _knowledge_sources_url(organization_id, workspace_id)

    return f"{base}/{knowledge_source_id}/faq-entries"


async def _setup_manual_faq_source(
    client: AsyncClient, prefix: str
) -> tuple[str, str, str, dict]:
    owner_headers, organization_id = await _org_with_owner(
        client, f"{prefix}@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = (
        await _create_manual_faq_source(
            client, organization_id, workspace["id"], owner_headers
        )
    ).json()

    return organization_id, workspace["id"], created["id"], owner_headers


async def test_create_manual_faq_source_returns_expected_shape(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client, "faq-create-shape@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await _create_manual_faq_source(
        client, organization_id, workspace["id"], owner_headers, name="General FAQ"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "manual_faq"
    assert body["status"] == "pending"
    assert body["name"] == "General FAQ"


async def test_create_manual_faq_source_requires_authentication(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client, "faq-create-anon@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace['id'])}/manual-faq",
        json={"name": "General FAQ"},
    )

    assert response.status_code == 401


async def test_create_manual_faq_source_is_forbidden_for_a_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "faq-create-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await _create_manual_faq_source(
        client, organization_id, workspace["id"], member_headers
    )

    assert response.status_code == 403


async def test_create_entry_succeeds_for_an_owner(client: AsyncClient) -> None:
    (
        organization_id,
        workspace_id,
        source_id,
        owner_headers,
    ) = await _setup_manual_faq_source(client, "faq-entry-create")

    response = await client.post(
        _faq_entries_url(organization_id, workspace_id, source_id),
        json={"question": "What are your hours?", "answer": "9am to 5pm."},
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["question"] == "What are your hours?"
    assert body["answer"] == "9am to 5pm."
    assert body["knowledge_source_id"] == source_id


async def test_create_entry_requires_authentication(client: AsyncClient) -> None:
    organization_id, workspace_id, source_id, _ = await _setup_manual_faq_source(
        client, "faq-entry-anon"
    )

    response = await client.post(
        _faq_entries_url(organization_id, workspace_id, source_id),
        json={"question": "Q?", "answer": "A."},
    )

    assert response.status_code == 401


async def test_create_entry_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "faq-entry-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    source = (
        await _create_manual_faq_source(
            client, organization_id, workspace["id"], owner_headers
        )
    ).json()

    response = await client.post(
        _faq_entries_url(organization_id, workspace["id"], source["id"]),
        json={"question": "Q?", "answer": "A."},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_list_returns_empty_for_a_new_source(client: AsyncClient) -> None:
    (
        organization_id,
        workspace_id,
        source_id,
        owner_headers,
    ) = await _setup_manual_faq_source(client, "faq-list-empty")

    response = await client.get(
        _faq_entries_url(organization_id, workspace_id, source_id),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_is_reachable_by_an_explicit_workspace_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "faq-list-member", "member"
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
        if m["user"]["email"] == "faq-list-member-member@example.com"
    )
    await client.post(
        f"{_workspaces_url(organization_id)}/{workspace['id']}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )
    source = (
        await _create_manual_faq_source(
            client, organization_id, workspace["id"], owner_headers
        )
    ).json()
    url = _faq_entries_url(organization_id, workspace["id"], source["id"])
    await client.post(
        url, json={"question": "Q?", "answer": "A."}, headers=owner_headers
    )

    response = await client.get(url, headers=member_headers)

    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_update_applies_a_partial_change_and_leaves_the_rest_untouched(
    client: AsyncClient,
) -> None:
    (
        organization_id,
        workspace_id,
        source_id,
        owner_headers,
    ) = await _setup_manual_faq_source(client, "faq-update-partial")
    url = _faq_entries_url(organization_id, workspace_id, source_id)
    created = (
        await client.post(
            url,
            json={"question": "What are your hours?", "answer": "9am to 5pm."},
            headers=owner_headers,
        )
    ).json()

    response = await client.patch(
        f"{url}/{created['id']}",
        json={"answer": "8am to 6pm."},
        headers=owner_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "8am to 6pm."
    assert body["question"] == "What are your hours?"


async def test_update_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "faq-update-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    source = (
        await _create_manual_faq_source(
            client, organization_id, workspace["id"], owner_headers
        )
    ).json()
    url = _faq_entries_url(organization_id, workspace["id"], source["id"])
    created = (
        await client.post(
            url, json={"question": "Q?", "answer": "A."}, headers=owner_headers
        )
    ).json()

    response = await client.patch(
        f"{url}/{created['id']}",
        json={"answer": "Updated."},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_update_is_not_found_for_a_nonexistent_entry(client: AsyncClient) -> None:
    (
        organization_id,
        workspace_id,
        source_id,
        owner_headers,
    ) = await _setup_manual_faq_source(client, "faq-update-missing")

    base_url = _faq_entries_url(organization_id, workspace_id, source_id)
    response = await client.patch(
        f"{base_url}/{_MISSING_ID}",
        json={"answer": "Updated."},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_delete_removes_the_row(client: AsyncClient) -> None:
    (
        organization_id,
        workspace_id,
        source_id,
        owner_headers,
    ) = await _setup_manual_faq_source(client, "faq-delete")
    url = _faq_entries_url(organization_id, workspace_id, source_id)
    created = (
        await client.post(
            url, json={"question": "Q?", "answer": "A."}, headers=owner_headers
        )
    ).json()

    delete_response = await client.delete(
        f"{url}/{created['id']}", headers=owner_headers
    )
    list_response = await client.get(url, headers=owner_headers)

    assert delete_response.status_code == 204
    assert list_response.json() == []


async def test_delete_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "faq-delete-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    source = (
        await _create_manual_faq_source(
            client, organization_id, workspace["id"], owner_headers
        )
    ).json()
    url = _faq_entries_url(organization_id, workspace["id"], source["id"])
    created = (
        await client.post(
            url, json={"question": "Q?", "answer": "A."}, headers=owner_headers
        )
    ).json()

    response = await client.delete(f"{url}/{created['id']}", headers=member_headers)

    assert response.status_code == 403


async def test_entry_routes_404_for_a_nonexistent_source(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client, "faq-sourcemissing@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    response = await client.get(
        _faq_entries_url(organization_id, workspace["id"], _MISSING_ID),
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_entry_routes_404_for_a_file_type_source(client: AsyncClient) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client, "faq-wrongtype-file@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    uploaded = (
        await client.post(
            _knowledge_sources_url(organization_id, workspace["id"]),
            files={"file": ("a.txt", b"hello", "text/plain")},
            headers=owner_headers,
        )
    ).json()

    response = await client.get(
        _faq_entries_url(organization_id, workspace["id"], uploaded["id"]),
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_entry_in_one_workspace_is_not_reachable_through_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    (
        organization_id,
        workspace_a_id,
        source_id,
        owner_headers,
    ) = await _setup_manual_faq_source(client, "faq-sibling-workspace")
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    url_a = _faq_entries_url(organization_id, workspace_a_id, source_id)
    created = (
        await client.post(
            url_a, json={"question": "Q?", "answer": "A."}, headers=owner_headers
        )
    ).json()

    url_b = _faq_entries_url(organization_id, workspace_b["id"], source_id)
    response = await client.patch(
        f"{url_b}/{created['id']}",
        json={"answer": "Updated."},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_entry_in_one_organization_is_not_reachable_through_another(
    client: AsyncClient,
) -> None:
    (
        organization_a_id,
        workspace_id,
        source_id,
        owner_a_headers,
    ) = await _setup_manual_faq_source(client, "faq-sibling-org-a")
    owner_b_headers, organization_b_id = await _org_with_owner(
        client, "faq-sibling-org-b@example.com"
    )

    url = _faq_entries_url(organization_b_id, workspace_id, source_id)
    response = await client.get(url, headers=owner_b_headers)

    assert response.status_code == 404
