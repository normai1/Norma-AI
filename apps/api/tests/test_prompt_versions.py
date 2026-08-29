import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PromptVersionImmutable
from app.models.prompt_version import PromptVersion
from tests.conftest import _org_with_owner, _signed_in

ORGS = "/api/v1/organizations"


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
        f"{ORGS}/{organization_id}/workspaces",
        json={"name": name},
        headers=owner_headers,
    )

    return response.json()


def _prompt_templates_url(organization_id: str, workspace_id: str) -> str:
    return f"{ORGS}/{organization_id}/workspaces/{workspace_id}/prompt-templates"


async def _create_prompt_template(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
    name: str,
) -> dict:
    response = await client.post(
        _prompt_templates_url(organization_id, workspace_id),
        json={"name": name, "use_case": "receptionist"},
        headers=headers,
    )

    return response.json()


def _versions_url(
    organization_id: str,
    workspace_id: str,
    prompt_template_id: str,
) -> str:
    base = _prompt_templates_url(organization_id, workspace_id)

    return f"{base}/{prompt_template_id}/versions"


_VALID_PAYLOAD = {"content": "Hello, thanks for calling {{workspace.name}}!"}


async def _setup_prompt_template(
    client: AsyncClient, prefix: str
) -> tuple[str, str, str, dict]:
    owner_headers, organization_id = await _org_with_owner(
        client, f"{prefix}@example.com"
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

    return organization_id, workspace["id"], created["id"], owner_headers


async def test_create_succeeds_and_assigns_version_one(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-create")
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, prompt_template_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["content"] == _VALID_PAYLOAD["content"]
    assert body["prompt_template_id"] == prompt_template_id


async def test_second_create_assigns_version_two(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-second")
    )
    url = _versions_url(organization_id, workspace_id, prompt_template_id)

    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)
    second = await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    assert second.json()["version"] == 2


async def test_create_requires_authentication(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, _ = (
        await _setup_prompt_template(client, "ptver-anon")
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, prompt_template_id),
        json=_VALID_PAYLOAD,
    )

    assert response.status_code == 401


async def test_create_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "ptver-member",
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

    response = await client.post(
        _versions_url(organization_id, workspace["id"], created["id"]),
        json=_VALID_PAYLOAD,
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_create_rejects_empty_content(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-empty")
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, prompt_template_id),
        json={"content": ""},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_list_returns_empty_for_a_template_with_no_versions(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-listempty")
    )

    response = await client.get(
        _versions_url(organization_id, workspace_id, prompt_template_id),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_and_get_are_reachable_by_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "ptver-listmember",
        "member",
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
        if m["user"]["email"] == "ptver-listmember-member@example.com"
    )
    await client.post(
        f"{ORGS}/{organization_id}/workspaces/{workspace['id']}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )
    url = _versions_url(organization_id, workspace["id"], created["id"])
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    list_response = await client.get(url, headers=member_headers)
    get_response = await client.get(f"{url}/1", headers=member_headers)

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert get_response.status_code == 200
    assert get_response.json()["version"] == 1


async def test_get_is_not_found_for_a_nonexistent_version(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-missing")
    )

    response = await client.get(
        f"{_versions_url(organization_id, workspace_id, prompt_template_id)}/99",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_version_in_one_template_is_not_reachable_through_a_sibling_template(
    client: AsyncClient,
) -> None:
    (
        organization_id,
        workspace_id,
        template_a_id,
        owner_headers,
    ) = await _setup_prompt_template(client, "ptver-sibling")
    template_b = await _create_prompt_template(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        "Second Template",
    )
    await client.post(
        _versions_url(organization_id, workspace_id, template_a_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    response = await client.get(
        f"{_versions_url(organization_id, workspace_id, template_b['id'])}/1",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_updating_a_version_row_directly_is_rejected(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """
    Proves the immutability guard actually fires - not just that no code
    path currently attempts an update.
    """

    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-immutable")
    )
    await client.post(
        _versions_url(organization_id, workspace_id, prompt_template_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    prompt_version = await db.scalar(
        select(PromptVersion).where(
            PromptVersion.prompt_template_id == prompt_template_id,
        ),
    )
    prompt_version.content = "Mutated"

    with pytest.raises(PromptVersionImmutable):
        await db.flush()


def _diff_url(
    organization_id: str,
    workspace_id: str,
    prompt_template_id: str,
    from_version: int,
    to_version: int,
) -> str:
    base = _versions_url(organization_id, workspace_id, prompt_template_id)

    return f"{base}/{from_version}/diff/{to_version}"


async def test_diff_returns_only_the_fields_that_changed(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-diff")
    )
    url = _versions_url(organization_id, workspace_id, prompt_template_id)

    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)
    await client.post(
        url,
        json={"content": "A different greeting entirely."},
        headers=owner_headers,
    )

    response = await client.get(
        _diff_url(organization_id, workspace_id, prompt_template_id, 1, 2),
        headers=owner_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["from_version"] == 1
    assert body["to_version"] == 2
    assert set(body["changes"].keys()) == {"content"}
    assert body["changes"]["content"] == {
        "previous": _VALID_PAYLOAD["content"],
        "current": "A different greeting entirely.",
    }


async def test_diff_between_a_version_and_itself_is_empty(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-diffsame")
    )
    url = _versions_url(organization_id, workspace_id, prompt_template_id)
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    response = await client.get(
        _diff_url(organization_id, workspace_id, prompt_template_id, 1, 1),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["changes"] == {}


async def test_diff_is_not_found_when_a_version_is_missing(client: AsyncClient) -> None:
    organization_id, workspace_id, prompt_template_id, owner_headers = (
        await _setup_prompt_template(client, "ptver-diffmissing")
    )
    url = _versions_url(organization_id, workspace_id, prompt_template_id)
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    response = await client.get(
        _diff_url(organization_id, workspace_id, prompt_template_id, 1, 99),
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_diff_is_reachable_by_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "ptver-diffmember",
        "member",
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
        if m["user"]["email"] == "ptver-diffmember-member@example.com"
    )
    await client.post(
        f"{ORGS}/{organization_id}/workspaces/{workspace['id']}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )
    created = await _create_prompt_template(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk Receptionist",
    )
    url = _versions_url(organization_id, workspace["id"], created["id"])
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    response = await client.get(
        _diff_url(organization_id, workspace["id"], created["id"], 1, 1),
        headers=member_headers,
    )

    assert response.status_code == 200
