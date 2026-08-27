from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, hash_token
from app.models.organization import Organization
from app.models.session import UserSession
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember


async def _make_user(db: AsyncSession, email: str = "person@example.com") -> User:
    user = User(
        email=email,
        password_hash=hash_password("a-real-password"),
        full_name="Test Person",
    )

    db.add(user)
    await db.flush()

    return user


async def _make_organization(db: AsyncSession, slug: str = "acme") -> Organization:
    organization = Organization(name="Acme", slug=slug)

    db.add(organization)
    await db.flush()

    return organization


async def test_user_persists_with_generated_id_and_timestamps(
    db: AsyncSession,
) -> None:
    user = await _make_user(db)

    assert user.id is not None
    assert user.created_at is not None
    assert user.updated_at is not None
    assert user.is_active is True
    assert user.last_login_at is None

    found = await db.scalar(select(User).where(User.email == "person@example.com"))

    assert found is not None
    assert found.id == user.id


async def test_duplicate_email_is_rejected(db: AsyncSession) -> None:
    await _make_user(db, "dupe@example.com")

    db.add(
        User(
            email="dupe@example.com",
            password_hash=hash_password("another-password"),
        )
    )

    with pytest.raises(IntegrityError):
        await db.flush()


async def test_session_persists_against_its_user(db: AsyncSession) -> None:
    user = await _make_user(db, "sessions@example.com")

    session = UserSession(
        user_id=user.id,
        token_hash=hash_token("a-refresh-token"),
        expires_at=datetime.now(UTC) + timedelta(days=30),
        user_agent="pytest",
        ip_address="127.0.0.1",
    )

    db.add(session)
    await db.flush()

    assert session.id is not None
    assert session.revoked_at is None

    found = await db.scalar(
        select(UserSession).where(UserSession.user_id == user.id),
    )

    assert found is not None
    assert found.token_hash == hash_token("a-refresh-token")


async def test_deleting_user_cascades_sessions(db: AsyncSession) -> None:
    user = await _make_user(db, "cascade@example.com")

    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token("cascade-token"),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await db.flush()

    await db.delete(user)
    await db.flush()

    remaining = await db.scalars(
        select(UserSession).where(UserSession.user_id == user.id),
    )

    assert remaining.all() == []


async def test_workspace_persists_with_generated_id_and_default_settings(
    db: AsyncSession,
) -> None:
    organization = await _make_organization(db, "workspace-defaults")

    workspace = Workspace(organization_id=organization.id, name="Downtown Clinic")

    db.add(workspace)
    await db.flush()

    assert workspace.id is not None
    assert workspace.created_at is not None
    assert workspace.settings == {}

    found = await db.scalar(
        select(Workspace).where(Workspace.organization_id == organization.id),
    )

    assert found is not None
    assert found.id == workspace.id


async def test_workspace_member_persists_against_its_workspace(
    db: AsyncSession,
) -> None:
    organization = await _make_organization(db, "workspace-members")
    user = await _make_user(db, "workspace-member@example.com")
    workspace = Workspace(organization_id=organization.id, name="Front Desk")

    db.add(workspace)
    await db.flush()

    member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id)

    db.add(member)
    await db.flush()

    assert member.id is not None

    found = await db.scalar(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id),
    )

    assert found is not None
    assert found.user_id == user.id


async def test_duplicate_workspace_membership_is_rejected(db: AsyncSession) -> None:
    organization = await _make_organization(db, "workspace-dupe-member")
    user = await _make_user(db, "dupe-workspace-member@example.com")
    workspace = Workspace(organization_id=organization.id, name="Back Office")

    db.add(workspace)
    await db.flush()

    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id))
    await db.flush()

    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id))

    with pytest.raises(IntegrityError):
        await db.flush()


async def test_deleting_workspace_cascades_members(db: AsyncSession) -> None:
    organization = await _make_organization(db, "workspace-cascade")
    user = await _make_user(db, "cascade-workspace-member@example.com")
    workspace = Workspace(organization_id=organization.id, name="Warehouse")

    db.add(workspace)
    await db.flush()

    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id))
    await db.flush()

    await db.delete(workspace)
    await db.flush()

    remaining = await db.scalars(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id),
    )

    assert remaining.all() == []
