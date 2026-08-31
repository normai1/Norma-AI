from httpx import AsyncClient

from app.providers.mock_web_crawler import MockPageFetcher
from tests.conftest import _org_with_owner, _signed_in

ORGS = "/api/v1/organizations"
_MISSING_ID = "00000000-0000-0000-0000-000000000000"


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


async def _create_website(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    assistant_id: str,
    url: str = "http://example.com/",
):
    return await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/website",
        json={"url": url, "assistant_id": assistant_id},
        headers=headers,
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


async def test_create_website_source_succeeds_for_an_owner(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_workspace(client, "ws-create-owner")
    )
    page_fetcher.pages["http://example.com/"] = _page('<a href="/about">About</a>')
    page_fetcher.pages["http://example.com/about"] = _page("About us.")

    response = await _create_website(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "website"
    assert body["status"] == "completed"
    assert body["error_message"] is None
    assert body["source_url"] == "http://example.com/"
    urls = {page["url"] for page in body["crawled_pages"]}
    assert urls == {"http://example.com/", "http://example.com/about"}
    assert body["document"] is None


async def test_create_website_source_with_an_unreachable_root_fails(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_workspace(client, "ws-create-unreachable")
    )
    # Deliberately nothing registered for this URL in page_fetcher.

    response = await _create_website(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        assistant_id,
        url="http://unreachable.example/",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"] is not None
    assert body["crawled_pages"] == []


async def test_create_website_source_enforces_the_page_count_cap(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_workspace(client, "ws-create-cap")
    )
    links = "".join(f'<a href="/page{i}">p{i}</a>' for i in range(30))
    page_fetcher.pages["http://example.com/"] = _page(links)
    for i in range(30):
        page_fetcher.pages[f"http://example.com/page{i}"] = _page(f"Page {i}.")

    response = await _create_website(
        client, organization_id, workspace_id, owner_headers, assistant_id
    )

    assert response.status_code == 201
    assert len(response.json()["crawled_pages"]) == 20


async def test_create_website_source_requires_authentication(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, _, _ = await _setup_workspace(
        client, "ws-create-anon"
    )

    response = await client.post(
        f"{_knowledge_sources_url(organization_id, workspace_id)}/website",
        json={"url": "http://example.com/"},
    )

    assert response.status_code == 401


async def test_create_website_source_is_forbidden_for_a_member(
    client: AsyncClient,
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "ws-create-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    assistant_id = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers
    )

    response = await _create_website(
        client, organization_id, workspace["id"], member_headers, assistant_id
    )

    assert response.status_code == 403


async def test_recrawl_updates_only_the_changed_page(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_workspace(client, "ws-recrawl-changed")
    )
    page_fetcher.pages["http://example.com/"] = _page('<a href="/about">About</a>')
    page_fetcher.pages["http://example.com/about"] = _page("About us, version one.")

    created = (
        await _create_website(
            client, organization_id, workspace_id, owner_headers, assistant_id
        )
    ).json()
    original_root_hash = next(
        p["content_hash"]
        for p in created["crawled_pages"]
        if p["url"] == "http://example.com/"
    )
    original_about_hash = next(
        p["content_hash"]
        for p in created["crawled_pages"]
        if p["url"] == "http://example.com/about"
    )

    # Only "/about"'s content changes for the recrawl.
    page_fetcher.pages["http://example.com/about"] = _page("About us, version two.")

    base_url = _knowledge_sources_url(organization_id, workspace_id)
    recrawl_response = await client.post(
        f"{base_url}/{created['id']}/recrawl", headers=owner_headers
    )

    assert recrawl_response.status_code == 200
    body = recrawl_response.json()
    root_hash = next(
        p["content_hash"]
        for p in body["crawled_pages"]
        if p["url"] == "http://example.com/"
    )
    about_hash = next(
        p["content_hash"]
        for p in body["crawled_pages"]
        if p["url"] == "http://example.com/about"
    )
    assert root_hash == original_root_hash
    assert about_hash != original_about_hash


async def test_recrawl_is_not_found_for_a_nonexistent_source(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, _, owner_headers = await _setup_workspace(
        client, "ws-recrawl-missing"
    )

    base_url = _knowledge_sources_url(organization_id, workspace_id)
    response = await client.post(
        f"{base_url}/{_MISSING_ID}/recrawl", headers=owner_headers
    )

    assert response.status_code == 404


async def test_recrawl_is_not_found_for_a_file_type_source(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = (
        await _setup_workspace(client, "ws-recrawl-wrongtype")
    )
    uploaded = (
        await client.post(
            _knowledge_sources_url(organization_id, workspace_id),
            files={"file": ("a.txt", b"hello", "text/plain")},
            data={"assistant_id": assistant_id},
            headers=owner_headers,
        )
    ).json()

    base_url = _knowledge_sources_url(organization_id, workspace_id)
    response = await client.post(
        f"{base_url}/{uploaded['id']}/recrawl", headers=owner_headers
    )

    assert response.status_code == 404


async def test_recrawl_is_forbidden_for_a_member(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client, "ws-recrawl-member", "member"
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    assistant_id = await _create_assistant(
        client, organization_id, workspace["id"], owner_headers
    )
    page_fetcher.pages["http://example.com/"] = _page("Hello.")
    created = (
        await _create_website(
            client, organization_id, workspace["id"], owner_headers, assistant_id
        )
    ).json()

    base_url = _knowledge_sources_url(organization_id, workspace["id"])
    response = await client.post(
        f"{base_url}/{created['id']}/recrawl", headers=member_headers
    )

    assert response.status_code == 403


async def test_website_source_in_one_workspace_is_not_reachable_through_a_sibling(
    client: AsyncClient, page_fetcher: MockPageFetcher
) -> None:
    organization_id, workspace_a_id, assistant_id, owner_headers = (
        await _setup_workspace(client, "ws-sibling-workspace")
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    page_fetcher.pages["http://example.com/"] = _page("Hello.")
    created = (
        await _create_website(
            client, organization_id, workspace_a_id, owner_headers, assistant_id
        )
    ).json()

    base_url = _knowledge_sources_url(organization_id, workspace_b["id"])
    response = await client.post(
        f"{base_url}/{created['id']}/recrawl", headers=owner_headers
    )

    assert response.status_code == 404


async def test_create_website_with_an_assistant_id_from_a_sibling_workspace_404s(
    client: AsyncClient,
) -> None:
    organization_id, workspace_a_id, _, owner_headers = await _setup_workspace(
        client, "ws-assistant-sibling-workspace"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    assistant_in_b = await _create_assistant(
        client, organization_id, workspace_b["id"], owner_headers
    )

    response = await _create_website(
        client, organization_id, workspace_a_id, owner_headers, assistant_in_b
    )

    assert response.status_code == 404
