from httpx import AsyncClient

from app.providers.mock_web_crawler import MockPageFetcher
from tests.conftest import _org_with_owner, _signed_in

ORGS = "/api/v1/organizations"


def _page(body: str) -> str:
    return f"<html><body>{body}</body></html>"


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


def _chunks_url(
    organization_id: str, workspace_id: str, knowledge_source_id: str
) -> str:
    base = _knowledge_sources_url(organization_id, workspace_id)

    return f"{base}/{knowledge_source_id}/chunks"


async def _upload(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    filename: str = "policy.txt",
    content: bytes = b"hello knowledge base",
    content_type: str = "text/plain",
):
    return await client.post(
        _knowledge_sources_url(organization_id, workspace_id),
        files={"file": (filename, content, content_type)},
        headers=headers,
    )


async def _create_website(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    url: str = "http://example.com/",
):
    return await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/website",
        json={"url": url},
        headers=headers,
    )


async def _setup_org_workspace(client: AsyncClient, prefix: str):
    owner_headers, organization_id = await _org_with_owner(
        client, f"{prefix}@example.com"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )

    return organization_id, workspace["id"], owner_headers


async def test_uploading_a_valid_txt_produces_chunks_reachable_via_the_api(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-upload-ok"
    )

    upload = await _upload(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        content=b"hello knowledge base",
    )

    assert upload.status_code == 201
    body = upload.json()
    assert body["status"] == "completed"
    assert body["document"]["processing_status"] == "completed"
    assert body["document"]["processing_error"] is None

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, body["id"]),
        headers=owner_headers,
    )

    assert chunks.status_code == 200
    chunk_list = chunks.json()
    assert len(chunk_list) == 1
    assert chunk_list[0]["text"] == "hello knowledge base"
    assert chunk_list[0]["ordering"] == 0
    assert chunk_list[0]["metadata"]["char_start"] == 0
    assert chunk_list[0]["metadata"]["char_end"] == len("hello knowledge base")


async def test_uploading_an_unparseable_pdf_marks_failed_with_zero_chunks(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-upload-fail"
    )

    upload = await _upload(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        filename="broken.pdf",
        content=b"not a real pdf at all",
        content_type="application/pdf",
    )

    assert upload.status_code == 201
    body = upload.json()
    assert body["status"] == "failed"
    assert body["error_message"]
    assert body["document"]["processing_status"] == "failed"
    assert body["document"]["processing_error"]

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, body["id"]),
        headers=owner_headers,
    )

    assert chunks.json() == []


async def test_process_reprocesses_without_duplicating_chunks(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-process-retry"
    )

    upload = await _upload(client, organization_id, workspace_id, owner_headers)
    source_id = upload.json()["id"]

    process = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/{source_id}/process",
        headers=owner_headers,
    )

    assert process.status_code == 200
    assert process.json()["status"] == "completed"

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, source_id),
        headers=owner_headers,
    )

    assert len(chunks.json()) == 1


async def test_process_422s_for_a_manual_faq_source(client: AsyncClient) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-process-wrong-type"
    )

    source = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/manual-faq",
        json={"name": "General FAQ"},
        headers=owner_headers,
    )
    source_id = source.json()["id"]

    process = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/{source_id}/process",
        headers=owner_headers,
    )

    assert process.status_code == 422


async def test_chunks_requires_authentication(client: AsyncClient) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-list-anon"
    )
    upload = await _upload(client, organization_id, workspace_id, owner_headers)

    response = await client.get(
        _chunks_url(organization_id, workspace_id, upload.json()["id"]),
    )

    assert response.status_code == 401


async def test_chunks_are_reachable_by_any_workspace_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "chunk-list-member", "viewer"
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
        if m["user"]["email"] == "chunk-list-member-viewer@example.com"
    )
    await client.post(
        f"{_workspaces_url(organization_id)}/{workspace['id']}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )
    upload = await _upload(client, organization_id, workspace["id"], owner_headers)

    response = await client.get(
        _chunks_url(organization_id, workspace["id"], upload.json()["id"]),
        headers=member_headers,
    )

    assert response.status_code == 200


async def test_chunks_404_for_a_source_in_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-list-sibling"
    )
    sibling_workspace = await _create_workspace(
        client, organization_id, owner_headers, "Sibling"
    )
    upload = await _upload(client, organization_id, workspace_id, owner_headers)

    response = await client.get(
        _chunks_url(organization_id, sibling_workspace["id"], upload.json()["id"]),
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_creating_a_website_source_produces_chunks_tagged_by_url(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-website-create"
    )
    page_fetcher.pages["http://example.com/"] = _page('<a href="/about">About</a>')
    page_fetcher.pages["http://example.com/about"] = _page("About us.")

    created = await _create_website(
        client, organization_id, workspace_id, owner_headers
    )
    source_id = created.json()["id"]

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, source_id),
        headers=owner_headers,
    )

    assert chunks.status_code == 200
    chunk_list = chunks.json()
    urls = {chunk["metadata"]["url"] for chunk in chunk_list}
    assert urls == {"http://example.com/", "http://example.com/about"}
    assert any("About us." in chunk["text"] for chunk in chunk_list)


async def test_recrawl_replaces_chunks_for_a_dropped_page(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-website-recrawl-drop"
    )
    page_fetcher.pages["http://example.com/"] = _page('<a href="/about">About</a>')
    page_fetcher.pages["http://example.com/about"] = _page("About us.")

    created = await _create_website(
        client, organization_id, workspace_id, owner_headers
    )
    source_id = created.json()["id"]

    # The recrawl no longer links to (or fetches) /about at all.
    page_fetcher.pages["http://example.com/"] = _page("No links here.")

    base_url = _knowledge_sources_url(organization_id, workspace_id)
    recrawl = await client.post(
        f"{base_url}/{source_id}/recrawl", headers=owner_headers
    )
    assert recrawl.status_code == 200

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, source_id),
        headers=owner_headers,
    )
    chunk_list = chunks.json()

    assert all(
        chunk["metadata"]["url"] != "http://example.com/about" for chunk in chunk_list
    )
    assert any("No links here." in chunk["text"] for chunk in chunk_list)


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


async def test_creating_a_faq_entry_produces_one_chunk(client: AsyncClient) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-faq-create"
    )
    source = await _create_manual_faq_source(
        client, organization_id, workspace_id, owner_headers
    )
    source_id = source.json()["id"]

    await client.post(
        _faq_entries_url(organization_id, workspace_id, source_id),
        json={"question": "What are your hours?", "answer": "9am to 5pm."},
        headers=owner_headers,
    )

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, source_id),
        headers=owner_headers,
    )
    chunk_list = chunks.json()

    assert len(chunk_list) == 1
    assert chunk_list[0]["text"] == "Q: What are your hours?\nA: 9am to 5pm."


async def test_updating_a_faq_entry_updates_its_chunk_in_place(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-faq-update"
    )
    source = await _create_manual_faq_source(
        client, organization_id, workspace_id, owner_headers
    )
    source_id = source.json()["id"]

    created = await client.post(
        _faq_entries_url(organization_id, workspace_id, source_id),
        json={"question": "What are your hours?", "answer": "9am to 5pm."},
        headers=owner_headers,
    )
    entry_id = created.json()["id"]

    await client.patch(
        f"{_faq_entries_url(organization_id, workspace_id, source_id)}/{entry_id}",
        json={"answer": "8am to 6pm."},
        headers=owner_headers,
    )

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, source_id),
        headers=owner_headers,
    )
    chunk_list = chunks.json()

    assert len(chunk_list) == 1
    assert chunk_list[0]["text"] == "Q: What are your hours?\nA: 8am to 6pm."


async def test_deleting_a_faq_entry_removes_its_chunk(client: AsyncClient) -> None:
    organization_id, workspace_id, owner_headers = await _setup_org_workspace(
        client, "chunk-faq-delete"
    )
    source = await _create_manual_faq_source(
        client, organization_id, workspace_id, owner_headers
    )
    source_id = source.json()["id"]

    created = await client.post(
        _faq_entries_url(organization_id, workspace_id, source_id),
        json={"question": "What are your hours?", "answer": "9am to 5pm."},
        headers=owner_headers,
    )
    entry_id = created.json()["id"]

    await client.delete(
        f"{_faq_entries_url(organization_id, workspace_id, source_id)}/{entry_id}",
        headers=owner_headers,
    )

    chunks = await client.get(
        _chunks_url(organization_id, workspace_id, source_id),
        headers=owner_headers,
    )

    assert chunks.json() == []
