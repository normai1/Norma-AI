import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantVersionImmutable
from app.models.assistant_version import AssistantVersion
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


def _assistants_url(organization_id: str, workspace_id: str) -> str:
    return f"{ORGS}/{organization_id}/workspaces/{workspace_id}/assistants"


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


def _versions_url(organization_id: str, workspace_id: str, assistant_id: str) -> str:
    return f"{_assistants_url(organization_id, workspace_id)}/{assistant_id}/versions"


_VALID_PAYLOAD = {
    "voice_id": "v1",
    "language": "en-US",
    "greeting": "Thanks for calling!",
    "persona": "Warm and efficient.",
    "speech_rate": 1.1,
    "turn_sensitivity": 0.6,
    "creativity": 0.4,
    "ambient_sound": None,
}


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
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )

    return organization_id, workspace["id"], created["id"], owner_headers


async def test_create_succeeds_and_assigns_version_one(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-create",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["voice_id"] == "v1"
    assert body["speech_rate"] == 1.1
    assert body["prompt_template_id"] is None
    assert body["prompt_version"] is None


async def test_second_create_assigns_version_two(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-second",
    )
    url = _versions_url(organization_id, workspace_id, assistant_id)

    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)
    second = await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    assert second.json()["version"] == 2


async def test_create_requires_authentication(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, _ = await _setup_assistant(
        client,
        "asstver-anon",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json=_VALID_PAYLOAD,
    )

    assert response.status_code == 401


async def test_create_is_forbidden_for_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asstver-member",
        "member",
    )
    workspace = await _create_workspace(
        client, organization_id, owner_headers, "Clinic"
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )

    response = await client.post(
        _versions_url(organization_id, workspace["id"], created["id"]),
        json=_VALID_PAYLOAD,
        headers=member_headers,
    )

    assert response.status_code == 403


async def test_create_rejects_out_of_bounds_speech_rate(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-badrate",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "speech_rate": 3.0},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_create_rejects_out_of_bounds_creativity(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-badcreativity",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "creativity": 1.5},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_create_rejects_out_of_bounds_turn_sensitivity(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-badsensitivity",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "turn_sensitivity": -0.1},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_list_returns_empty_for_an_assistant_with_no_versions(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-listempty",
    )

    response = await client.get(
        _versions_url(organization_id, workspace_id, assistant_id),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_and_get_are_reachable_by_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asstver-listmember",
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
        if m["user"]["email"] == "asstver-listmember-member@example.com"
    )
    await client.post(
        f"{ORGS}/{organization_id}/workspaces/{workspace['id']}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
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
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-missing",
    )

    response = await client.get(
        f"{_versions_url(organization_id, workspace_id, assistant_id)}/99",
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_version_in_one_assistant_is_not_reachable_through_a_sibling_assistant(
    client: AsyncClient,
) -> None:
    (
        organization_id,
        workspace_id,
        assistant_a_id,
        owner_headers,
    ) = await _setup_assistant(
        client,
        "asstver-sibling",
    )
    assistant_b = await _create_assistant(
        client,
        organization_id,
        workspace_id,
        owner_headers,
        "Second Desk",
    )
    await client.post(
        _versions_url(organization_id, workspace_id, assistant_a_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    response = await client.get(
        f"{_versions_url(organization_id, workspace_id, assistant_b['id'])}/1",
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

    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-immutable",
    )
    await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json=_VALID_PAYLOAD,
        headers=owner_headers,
    )

    assistant_version = await db.scalar(
        select(AssistantVersion).where(AssistantVersion.assistant_id == assistant_id),
    )
    assistant_version.greeting = "Mutated"

    with pytest.raises(AssistantVersionImmutable):
        await db.flush()


def _diff_url(
    organization_id: str,
    workspace_id: str,
    assistant_id: str,
    from_version: int,
    to_version: int,
) -> str:
    base = _versions_url(organization_id, workspace_id, assistant_id)

    return f"{base}/{from_version}/diff/{to_version}"


async def test_diff_returns_only_the_fields_that_changed(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-diff",
    )
    url = _versions_url(organization_id, workspace_id, assistant_id)

    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)
    await client.post(
        url,
        json={**_VALID_PAYLOAD, "greeting": "Hello there!", "speech_rate": 1.5},
        headers=owner_headers,
    )

    response = await client.get(
        _diff_url(organization_id, workspace_id, assistant_id, 1, 2),
        headers=owner_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["from_version"] == 1
    assert body["to_version"] == 2
    assert set(body["changes"].keys()) == {"greeting", "speech_rate"}
    assert body["changes"]["greeting"] == {
        "previous": "Thanks for calling!",
        "current": "Hello there!",
    }
    assert body["changes"]["speech_rate"] == {"previous": 1.1, "current": 1.5}


async def test_diff_between_a_version_and_itself_is_empty(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-diffsame",
    )
    url = _versions_url(organization_id, workspace_id, assistant_id)
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    response = await client.get(
        _diff_url(organization_id, workspace_id, assistant_id, 1, 1),
        headers=owner_headers,
    )

    assert response.status_code == 200
    assert response.json()["changes"] == {}


async def test_diff_is_not_found_when_a_version_is_missing(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-diffmissing",
    )
    url = _versions_url(organization_id, workspace_id, assistant_id)
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    response = await client.get(
        _diff_url(organization_id, workspace_id, assistant_id, 1, 99),
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_diff_is_reachable_by_a_member(client: AsyncClient) -> None:
    organization_id, owner_headers, member_headers = await _org_with_member(
        client,
        "asstver-diffmember",
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
        if m["user"]["email"] == "asstver-diffmember-member@example.com"
    )
    await client.post(
        f"{ORGS}/{organization_id}/workspaces/{workspace['id']}/members",
        json={"member_id": member_id},
        headers=owner_headers,
    )
    created = await _create_assistant(
        client,
        organization_id,
        workspace["id"],
        owner_headers,
        "Front Desk",
    )
    url = _versions_url(organization_id, workspace["id"], created["id"])
    await client.post(url, json=_VALID_PAYLOAD, headers=owner_headers)

    response = await client.get(
        _diff_url(organization_id, workspace["id"], created["id"], 1, 1),
        headers=member_headers,
    )

    assert response.status_code == 200


def _prompt_templates_url(organization_id: str, workspace_id: str) -> str:
    return f"{ORGS}/{organization_id}/workspaces/{workspace_id}/prompt-templates"


async def _create_prompt_template_with_version(
    client: AsyncClient,
    organization_id: str,
    workspace_id: str,
    headers: dict[str, str],
) -> tuple[str, int]:
    """
    Create a prompt template and one version of it. Returns the template id
    and the version number.
    """

    template_response = await client.post(
        _prompt_templates_url(organization_id, workspace_id),
        json={"name": "Front Desk Receptionist", "use_case": "receptionist"},
        headers=headers,
    )
    template = template_response.json()

    base_url = _prompt_templates_url(organization_id, workspace_id)
    version_response = await client.post(
        f"{base_url}/{template['id']}/versions",
        json={"content": "Thanks for calling!"},
        headers=headers,
    )
    version = version_response.json()

    return template["id"], version["version"]


async def test_create_with_a_prompt_reference_succeeds_and_echoes_it_back(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-promptref",
    )
    prompt_template_id, prompt_version = await _create_prompt_template_with_version(
        client, organization_id, workspace_id, owner_headers
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={
            **_VALID_PAYLOAD,
            "prompt_template_id": prompt_template_id,
            "prompt_version": prompt_version,
        },
        headers=owner_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["prompt_template_id"] == prompt_template_id
    assert body["prompt_version"] == prompt_version


async def test_create_rejects_prompt_version_without_prompt_template_id(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-promptref-partial",
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "prompt_version": 1},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_create_rejects_prompt_template_id_without_prompt_version(
    client: AsyncClient,
) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-promptref-partial-b",
    )
    prompt_template_id, _ = await _create_prompt_template_with_version(
        client, organization_id, workspace_id, owner_headers
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={**_VALID_PAYLOAD, "prompt_template_id": prompt_template_id},
        headers=owner_headers,
    )

    assert response.status_code == 422


async def test_create_rejects_a_nonexistent_prompt_version(client: AsyncClient) -> None:
    organization_id, workspace_id, assistant_id, owner_headers = await _setup_assistant(
        client,
        "asstver-promptref-badversion",
    )
    prompt_template_id, _ = await _create_prompt_template_with_version(
        client, organization_id, workspace_id, owner_headers
    )

    response = await client.post(
        _versions_url(organization_id, workspace_id, assistant_id),
        json={
            **_VALID_PAYLOAD,
            "prompt_template_id": prompt_template_id,
            "prompt_version": 99,
        },
        headers=owner_headers,
    )

    assert response.status_code == 404


async def test_create_rejects_a_prompt_template_from_a_sibling_workspace(
    client: AsyncClient,
) -> None:
    owner_headers, organization_id = await _org_with_owner(
        client,
        "asstver-promptref-sibling@example.com",
    )
    workspace_a = await _create_workspace(
        client, organization_id, owner_headers, "Clinic A"
    )
    workspace_b = await _create_workspace(
        client, organization_id, owner_headers, "Clinic B"
    )
    assistant = await _create_assistant(
        client,
        organization_id,
        workspace_a["id"],
        owner_headers,
        "Front Desk",
    )
    prompt_template_id, prompt_version = await _create_prompt_template_with_version(
        client, organization_id, workspace_b["id"], owner_headers
    )

    response = await client.post(
        _versions_url(organization_id, workspace_a["id"], assistant["id"]),
        json={
            **_VALID_PAYLOAD,
            "prompt_template_id": prompt_template_id,
            "prompt_version": prompt_version,
        },
        headers=owner_headers,
    )

    assert response.status_code == 404
