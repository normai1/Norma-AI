from httpx import AsyncClient

from app.providers.mock_storage import MockStorage
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


def _assistants_url(organization_id: str, workspace_id: str) -> str:
    return f"{_workspaces_url(organization_id)}/{workspace_id}/assistants"


async def _create_assistant(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    name: str = "Front Desk",
) -> str:
    response = await client.post(
        _assistants_url(organization_id, workspace_id),
        json={"name": name},
        headers=headers,
    )

    return response.json()["id"]


async def _upload(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    assistant_id: str,
    filename: str = "policy.txt",
    content: bytes = b"hello world",
    content_type: str = "text/plain",
):
    return await client.post(
        _knowledge_sources_url(organization_id, workspace_id),
        files={"file": (filename, content, content_type)},
        data={"assistant_id": assistant_id},
        headers=headers,
    )


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


async def _setup_workspace(
    client: AsyncClient, prefix: str
) -> tuple[str, str, str, dict]:
    owner_headers, organization_id = await _org_with_owner(
        client, f"{prefix}@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    assistant_id = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers
    )

    return organization_id, workspace["id"], assistant_id, owner_headers


async def test_upload_succeeds_for_an_owner(
    client: AsyncClient, storage: MockStorage
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_workspace(
        client, "ks-upload-owner"
    )

    response = await _upload(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        assistant_id,
        filename="policy.txt",
        content=b"the actual file bytes",
        content_type="text/plain",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "file"
    # Item 17 wires synchronous parsing/chunking into upload - a plain .txt
    # upload completes immediately rather than staying 'pending'.
    assert body["status"] == "completed"
    assert body["error_message"] is None
    assert body["organization_id"] == organization_id
    assert body["workspace_id"] == workspace_id
    assert body["document"]["filename"] == "policy.txt"
    assert body["document"]["content_type"] == "text/plain"
    assert body["document"]["processing_status"] == "completed"
    assert body["document"]["processing_error"] is None
    assert "storage_key" not in body["document"]

    # The bytes actually landed in storage, not just a DB row.
    assert len(storage.objects) == 1
    stored_bytes = next(iter(storage.objects.values()))
    assert stored_bytes == b"the actual file bytes"


async def test_upload_accepts_every_supported_extension(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_workspace(
        client, "ks-upload-extensions"
    )

    for filename, content_type in [
        ("a.pdf", "application/pdf"),
        (
            "b.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("c.md", "text/markdown"),
        ("d.txt", "text/plain"),
    ]:
        response = await _upload(
            client,
            organization_id,
            workspace_id,
            owner_headers,
            assistant_id,
            filename=filename,
            content_type=content_type,
        )

        assert response.status_code == 201, filename


async def test_upload_rejects_an_unsupported_extension(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_workspace(
        client, "ks-upload-badext"
    )

    response = await _upload(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        assistant_id,
        filename="virus.exe",
        content_type="application/octet-stream",
    )

    assert response.status_code == 422


async def test_upload_rejects_a_file_over_the_size_cap(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_workspace(
        client, "ks-upload-toolarge"
    )

    oversized = b"a" * (20 * 1024 * 1024 + 1)

    response = await _upload(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        assistant_id,
        filename="big.txt",
        content=oversized,
        content_type="text/plain",
    )

    assert response.status_code == 422


async def test_upload_requires_authentication(client: AsyncClient) -> None:
    organization_id, workspace_id, _, _ = await _setup_workspace(
        client, "ks-upload-anon"
    )

    response = await client.post(
        _knowledge_sources_url(organization_id, workspace_id),
        files={"file": ("a.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 401


async def test_upload_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "ks-upload-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    assistant_id = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers
    )

    response = await _upload(
        client, organization_id, workspace["id"], member_headers, assistant_id
    )

    assert response.status_code == 403


async def test_upload_is_forbidden_for_a_viewer(client: AsyncClient) -> None:
    organization_id, owner_headers, viewer_headers = await _org_with_member(
        client, "ks-upload-viewer", "viewer"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    assistant_id = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers
    )

    response = await _upload(
        client, organization_id, workspace["id"], viewer_headers, assistant_id
    )

    assert response.status_code == 403


async def test_list_returns_empty_for_a_new_workspace(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_workspace(
        client, "ks-list-empty"
    )

    response = await client.get(
        _knowledge_sources_url(organization_id, workspace_id),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_and_get_are_reachable_by_an_explicit_workspace_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "ks-list-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    member_id = await _org_member_id(
        client, organization_id, owner_headers, "ks-list-member-member@example.com"
    )
    await _grant_workspace_access(
        client, organization_id, workspace["id"], owner_headers, member_id
    )
    assistant_id = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers
    )
    created = (
        await _upload(
            client, organization_id, workspace["id"], owner_headers, assistant_id
        )
    ).json()

    list_response = await client.get(
        _knowledge_sources_url(organization_id, workspace["id"]),
        headers=member_headers,
    )
    base_url = _knowledge_sources_url(organization_id, workspace["id"])
    get_response = await client.get(
        f"{base_url}/{created['id']}",
        headers=member_headers,
    )

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]


async def test_get_is_not_found_for_a_nonexistent_knowledge_source(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_workspace(
        client, "ks-get-missing"
    )

    base_url = _knowledge_sources_url(organization_id, workspace_id)
    response = await client.get(f"{base_url}/{_MISSING_ID}", headers=owner_headers)

    assert response.status_code == 404


async def test_knowledge_source_in_one_workspace_is_not_reachable_through_a_sibling(
    client: AsyncClient,
) -> None:
    organization_id, workspace_a_id, assistant_id, owner_headers = (
        await _setup_workspace(client, "ks-sibling-workspace")
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    created = (
        await _upload(
            client, organization_id, workspace_a_id, owner_headers, assistant_id
        )
    ).json()

    base_url = _knowledge_sources_url(organization_id, workspace_b["id"])
    response = await client.get(f"{base_url}/{created['id']}", headers=owner_headers)

    assert response.status_code == 404


async def test_knowledge_source_in_one_organization_is_not_reachable_through_another(
    client: AsyncClient,
) -> None:
    organization_a_id, workspace_id, assistant_id, owner_a_headers = (
        await _setup_workspace(client, "ks-sibling-org-a")
    )
    owner_b_headers, organization_b_id = await _org_with_owner(
        client, "ks-sibling-org-b@example.com"
    )
    created = (
        await _upload(
            client, organization_a_id, workspace_id, owner_a_headers, assistant_id
        )
    ).json()

    base_url = _knowledge_sources_url(organization_b_id, workspace_id)
    response = await client.get(f"{base_url}/{created['id']}", headers=owner_b_headers)

    assert response.status_code == 404


async def test_upload_with_an_assistant_id_from_a_sibling_workspace_404s(
    client: AsyncClient,
) -> None:
    organization_id, workspace_a_id, _, owner_headers = await _setup_workspace(
        client, "ks-assistant-sibling-workspace"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    assistant_in_b = await _create_assistant(
        client, organization_id, workspace_b["id"], owner_headers
    )

    response = await _upload(
        client, organization_id, workspace_a_id, owner_headers, assistant_in_b
    )

    assert response.status_code == 404
