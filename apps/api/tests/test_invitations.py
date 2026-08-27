import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.models.invitation import Invitation
from tests.conftest import _org_with_owner, _signed_in

REGISTER = "/api/v1/auth/register"
ORGS = "/api/v1/organizations"
ACCEPT = "/api/v1/invitations/accept"


async def test_invite_returns_201_with_a_token(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "inviter@example.com")

    response = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "Invitee@Example.com", "role": "member"},
        headers=headers,
    )

    assert response.status_code == 201

    body = response.json()

    assert body["email"] == "invitee@example.com"
    assert body["role"] == "member"
    assert body["status"] == "pending"
    assert body["token"]


async def test_invitation_token_is_stored_hashed(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    headers, org_id = await _org_with_owner(client, "hashcheck@example.com")

    created = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "hashed-invitee@example.com"},
        headers=headers,
    )
    token = created.json()["token"]

    invitation = await db.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(token)),
    )

    assert invitation is not None
    assert invitation.token_hash != token


async def test_list_invitations_never_exposes_tokens(
    client: AsyncClient,
) -> None:
    headers, org_id = await _org_with_owner(client, "listinv@example.com")

    await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "listed@example.com"},
        headers=headers,
    )

    response = await client.get(f"{ORGS}/{org_id}/invitations", headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert "token" not in response.json()[0]


async def test_accept_adds_the_invitee_as_a_member(client: AsyncClient) -> None:
    owner, org_id = await _org_with_owner(client, "acc-owner@example.com")
    invitee = await _signed_in(client, "acc-invitee@example.com")

    created = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "acc-invitee@example.com", "role": "admin"},
        headers=owner,
    )

    response = await client.post(
        ACCEPT,
        json={"token": created.json()["token"]},
        headers=invitee,
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"

    roster = await client.get(f"{ORGS}/{org_id}/members", headers=owner)

    assert len(roster.json()) == 2


async def test_accepted_invitation_cannot_be_reused(client: AsyncClient) -> None:
    owner, org_id = await _org_with_owner(client, "reuse-owner@example.com")
    invitee = await _signed_in(client, "reuse-invitee@example.com")

    created = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "reuse-invitee@example.com"},
        headers=owner,
    )
    token = created.json()["token"]

    await client.post(ACCEPT, json={"token": token}, headers=invitee)
    replay = await client.post(ACCEPT, json={"token": token}, headers=invitee)

    assert replay.status_code == 400


async def test_accept_rejects_a_different_signed_in_user(
    client: AsyncClient,
) -> None:
    owner, org_id = await _org_with_owner(client, "mismatch-owner@example.com")
    wrong_user = await _signed_in(client, "wrong-person@example.com")

    created = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "intended-person@example.com"},
        headers=owner,
    )

    response = await client.post(
        ACCEPT,
        json={"token": created.json()["token"]},
        headers=wrong_user,
    )

    assert response.status_code == 403


async def test_accept_rejects_an_unknown_token(client: AsyncClient) -> None:
    headers = await _signed_in(client, "unknown-token@example.com")

    response = await client.post(
        ACCEPT,
        json={"token": "not-a-real-token"},
        headers=headers,
    )

    assert response.status_code == 400


async def test_accept_rejects_an_expired_invitation(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    owner, org_id = await _org_with_owner(client, "exp-owner@example.com")
    invitee = await _signed_in(client, "exp-invitee@example.com")

    created = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "exp-invitee@example.com"},
        headers=owner,
    )
    token = created.json()["token"]

    invitation = await db.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(token)),
    )
    invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.flush()

    response = await client.post(ACCEPT, json={"token": token}, headers=invitee)

    assert response.status_code == 400


async def test_revoked_invitation_cannot_be_accepted(client: AsyncClient) -> None:
    owner, org_id = await _org_with_owner(client, "rev-owner@example.com")
    invitee = await _signed_in(client, "rev-invitee@example.com")

    created = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "rev-invitee@example.com"},
        headers=owner,
    )
    body = created.json()

    revoked = await client.delete(
        f"{ORGS}/{org_id}/invitations/{body['id']}",
        headers=owner,
    )

    assert revoked.status_code == 204

    response = await client.post(
        ACCEPT,
        json={"token": body["token"]},
        headers=invitee,
    )

    assert response.status_code == 400


async def test_reinviting_supersedes_the_previous_token(
    client: AsyncClient,
) -> None:
    owner, org_id = await _org_with_owner(client, "resend-owner@example.com")
    invitee = await _signed_in(client, "resend-invitee@example.com")

    first = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "resend-invitee@example.com"},
        headers=owner,
    )
    second = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "resend-invitee@example.com"},
        headers=owner,
    )

    stale = await client.post(
        ACCEPT,
        json={"token": first.json()["token"]},
        headers=invitee,
    )

    assert stale.status_code == 400

    fresh = await client.post(
        ACCEPT,
        json={"token": second.json()["token"]},
        headers=invitee,
    )

    assert fresh.status_code == 200


async def test_cannot_invite_an_existing_member(client: AsyncClient) -> None:
    owner, org_id = await _org_with_owner(client, "dupe-owner@example.com")

    response = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "dupe-owner@example.com"},
        headers=owner,
    )

    assert response.status_code == 409


async def test_invite_rejects_a_malformed_email(client: AsyncClient) -> None:
    headers, org_id = await _org_with_owner(client, "bademail@example.com")

    response = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "not-an-email"},
        headers=headers,
    )

    assert response.status_code == 422


async def test_invite_rejects_a_non_member(client: AsyncClient) -> None:
    _, org_id = await _org_with_owner(client, "inv-owner@example.com")
    outsider = await _signed_in(client, "inv-outsider@example.com")

    response = await client.post(
        f"{ORGS}/{org_id}/invitations",
        json={"email": "someone@example.com"},
        headers=outsider,
    )

    assert response.status_code == 404


async def test_invitation_routes_require_authentication(
    client: AsyncClient,
) -> None:
    org_id = uuid.uuid4()

    assert (
        await client.post(f"{ORGS}/{org_id}/invitations", json={"email": "a@b.com"})
    ).status_code == 401
    assert (await client.get(f"{ORGS}/{org_id}/invitations")).status_code == 401
    assert (await client.post(ACCEPT, json={"token": "x"})).status_code == 401
