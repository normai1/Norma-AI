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


def _assistants_url(organization_id: str, workspace_id: str) -> str:
    return f"{_workspaces_url(organization_id)}/{workspace_id}/assistants"


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


def _glossary_url(organization_id: str, workspace_id: str, assistant_id: str) -> str:
    base = _assistants_url(organization_id, workspace_id)

    return f"{base}/{assistant_id}/glossary"


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
        client, organization_id, workspace["id"], owner_headers, "Front Desk"
    )

    return organization_id, workspace["id"], created["id"], owner_headers


async def test_create_succeeds_for_an_owner(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-create-owner"
    )

    response = await client.post(
        _glossary_url(organization_id, workspace_id, assistant_id),
        json={
            "term": "acetaminophen",
            "meaning": "a common pain reliever",
            "phonetic_spelling": "uh-SEE-tuh-MIN-oh-fen",
            "stt_boost_weight": 0.8,
        },
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["term"] == "acetaminophen"
    assert body["meaning"] == "a common pain reliever"
    assert body["phonetic_spelling"] == "uh-SEE-tuh-MIN-oh-fen"
    assert body["stt_boost_weight"] == 0.8
    assert body["assistant_id"] == assistant_id


async def test_create_uses_default_boost_weight(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-create-default"
    )

    response = await client.post(
        _glossary_url(organization_id, workspace_id, assistant_id),
        json={"term": "acme"},
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["stt_boost_weight"] == 0.5
    assert body["meaning"] is None
    assert body["phonetic_spelling"] is None


async def test_create_requires_authentication(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, _ = await _setup_assistant(
        client, "glossary-create-anon"
    )

    response = await client.post(
        _glossary_url(organization_id, workspace_id, assistant_id),
        json={"term": "acme"},
    )

    assert response.status_code == 401


async def test_create_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "glossary-create-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers, "Front Desk"
    )

    response = await client.post(
        _glossary_url(organization_id, workspace["id"], created["id"]),
        json={"term": "acme"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_create_is_forbidden_for_a_viewer(client: AsyncClient) -> None:
    organization_id, owner_headers, viewer_headers = await _org_with_member(
        client, "glossary-create-viewer", "viewer"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers, "Front Desk"
    )

    response = await client.post(
        _glossary_url(organization_id, workspace["id"], created["id"]),
        json={"term": "acme"},
        headers=viewer_headers,
    )

    assert response.status_code == 403


async def test_create_rejects_out_of_bounds_boost_weight(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-create-badweight"
    )

    response = await client.post(
        _glossary_url(organization_id, workspace_id, assistant_id),
        json={"term": "acme", "stt_boost_weight": 1.5},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_create_rejects_a_duplicate_term_on_the_same_assistant(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-create-dup"
    )
    url = _glossary_url(organization_id, workspace_id, assistant_id)
    await client.post(url, json={"term": "acme"}, headers=owner_headers)

    response = await client.post(url, json={"term": "acme"}, headers=owner_headers)

    assert response.status_code == 409


async def test_create_allows_the_same_term_on_a_different_assistant(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-create-samedup"
    )
    other_assistant = await _create_assistant(
        client, organization_id, workspace_id, owner_headers, "Second Desk"
    )
    await client.post(
        _glossary_url(organization_id, workspace_id, assistant_id),
        json={"term": "acme"},
        headers=owner_headers,
    )

    response = await client.post(
        _glossary_url(organization_id, workspace_id, other_assistant["id"]),
        json={"term": "acme"},
        headers=owner_headers,
    )

    assert response.status_code == 201


async def test_list_returns_empty_for_a_new_assistant(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-list-empty"
    )

    response = await client.get(
        _glossary_url(organization_id, workspace_id, assistant_id),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_is_reachable_by_an_explicit_workspace_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "glossary-list-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    member_id = await _org_member_id(
        client,
        organization_id,
        owner_headers,
        "glossary-list-member-member@example.com",
    )
    await _grant_workspace_access(
        client, organization_id, workspace["id"], owner_headers, member_id
    )
    created = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers, "Front Desk"
    )
    url = _glossary_url(organization_id, workspace["id"], created["id"])
    await client.post(url, json={"term": "acme"}, headers=owner_headers)

    response = await client.get(url, headers=member_headers)

    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_update_applies_a_partial_change_and_leaves_the_rest_untouched(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-update-partial"
    )
    url = _glossary_url(organization_id, workspace_id, assistant_id)
    created = (
        await client.post(
            url,
            json={
                "term": "acme",
                "meaning": "a company",
                "phonetic_spelling": "AK-mee",
                "stt_boost_weight": 0.5,
            },
            headers=owner_headers,
        )
    ).json()

    response = await client.patch(
        f"{url}/{created['id']}",
        json={"meaning": "a fictional company"},
        headers=owner_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meaning"] == "a fictional company"
    assert body["term"] == "acme"
    assert body["phonetic_spelling"] == "AK-mee"
    assert body["stt_boost_weight"] == 0.5


async def test_update_can_clear_a_nullable_field(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-update-clear"
    )
    url = _glossary_url(organization_id, workspace_id, assistant_id)
    created = (
        await client.post(
            url,
            json={"term": "acme", "meaning": "a company"},
            headers=owner_headers,
        )
    ).json()

    response = await client.patch(
        f"{url}/{created['id']}",
        json={"meaning": None},
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["meaning"] is None


async def test_update_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "glossary-update-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created_assistant = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers, "Front Desk"
    )
    url = _glossary_url(organization_id, workspace["id"], created_assistant["id"])
    created_entry = (
        await client.post(url, json={"term": "acme"}, headers=owner_headers)
    ).json()

    response = await client.patch(
        f"{url}/{created_entry['id']}",
        json={"meaning": "updated"},
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_update_is_not_found_for_a_nonexistent_entry(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-update-missing"
    )

    response = await client.patch(
        f"{_glossary_url(organization_id, workspace_id, assistant_id)}/{_MISSING_ID}",
        json={"meaning": "updated"},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_update_rejects_renaming_to_a_duplicate_term(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-update-dup"
    )
    url = _glossary_url(organization_id, workspace_id, assistant_id)
    await client.post(url, json={"term": "acme"}, headers=owner_headers)
    second = (
        await client.post(url, json={"term": "beta"}, headers=owner_headers)
    ).json()

    response = await client.patch(
        f"{url}/{second['id']}",
        json={"term": "acme"},
        headers=owner_headers,
    )

    assert response.status_code == 409


async def test_entry_in_one_assistant_is_not_reachable_through_a_sibling_assistant(
    client: AsyncClient,
) -> None:
    (
        organization_id,
        workspace_id,
        assistant_a_id,
        owner_headers,
    ) = await _setup_assistant(client, "glossary-sibling")
    assistant_b = await _create_assistant(
        client, organization_id, workspace_id, owner_headers, "Second Desk"
    )
    created = (
        await client.post(
            _glossary_url(organization_id, workspace_id, assistant_a_id),
            json={"term": "acme"},
            headers=owner_headers,
        )
    ).json()

    base_url = _glossary_url(organization_id, workspace_id, assistant_b["id"])
    response = await client.patch(
        f"{base_url}/{created['id']}",
        json={"meaning": "updated"},
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_delete_removes_the_row(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-delete"
    )
    url = _glossary_url(organization_id, workspace_id, assistant_id)
    created = (
        await client.post(url, json={"term": "acme"}, headers=owner_headers)
    ).json()

    delete_response = await client.delete(
        f"{url}/{created['id']}", headers=owner_headers
    )
    list_response = await client.get(url, headers=owner_headers)

    assert delete_response.status_code == 204
    assert list_response.json() == []


async def test_delete_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "glossary-delete-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created_assistant = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers, "Front Desk"
    )
    url = _glossary_url(organization_id, workspace["id"], created_assistant["id"])
    created_entry = (
        await client.post(url, json={"term": "acme"}, headers=owner_headers)
    ).json()

    response = await client.delete(
        f"{url}/{created_entry['id']}", headers=member_headers
    )

    assert response.status_code == 403


async def test_delete_is_not_found_for_a_nonexistent_entry(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-delete-missing"
    )

    response = await client.delete(
        f"{_glossary_url(organization_id, workspace_id, assistant_id)}/{_MISSING_ID}",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_entry_in_one_workspace_is_not_reachable_through_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client, "glossary-sibling-ws"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    created = (
        await client.post(
            _glossary_url(organization_id, workspace_id, assistant_id),
            json={"term": "acme"},
            headers=owner_headers,
        )
    ).json()

    response = await client.get(
        _glossary_url(organization_id, workspace_b["id"], assistant_id),
        headers=owner_headers,
    )

    assert response.status_code == 404
    assert created["term"] == "acme"


async def test_entry_in_one_organization_is_not_reachable_through_another(
    client: AsyncClient,
) -> None:
    (
        organization_a_id,
        workspace_id,
        assistant_id,
        owner_a_headers,
    ) = await _setup_assistant(client, "glossary-sibling-org-a")
    owner_b_headers, organization_b_id = await _org_with_owner(
        client, "glossary-sibling-org-b@example.com"
    )
    created = (
        await client.post(
            _glossary_url(organization_a_id, workspace_id, assistant_id),
            json={"term": "acme"},
            headers=owner_a_headers,
        )
    ).json()

    response = await client.get(
        f"{_glossary_url(organization_b_id, workspace_id, assistant_id)}",
        headers=owner_b_headers,
    )

    assert response.status_code == 404
    assert created["term"] == "acme"
