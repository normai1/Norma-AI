import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User
from app.repositories import organization as organization_repo
from app.services import organization as organization_service


async def _make_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash=hash_password("a-strong-password"))

    db.add(user)
    await db.flush()

    return user


async def test_create_organization_returns_slugged_organization(
    db: AsyncSession,
) -> None:
    owner = await _make_user(db, "owner@example.com")

    organization, member = await organization_service.create_organization(
        db,
        name="Acme Corp",
        owner_id=owner.id,
    )

    assert organization.id is not None
    assert organization.name == "Acme Corp"
    assert organization.slug == "acme-corp"
    assert organization.status == "active"
    assert organization.settings == {}

    assert member.organization_id == organization.id
    assert member.user_id == owner.id
    assert member.role == "owner"


async def test_creator_membership_is_persisted(db: AsyncSession) -> None:
    owner = await _make_user(db, "persisted@example.com")

    organization, _ = await organization_service.create_organization(
        db,
        name="Persisted Co",
        owner_id=owner.id,
    )

    found = await organization_service.get_membership(
        db,
        organization.id,
        owner.id,
    )

    assert found is not None
    assert found.role == "owner"


async def test_same_name_produces_distinct_slugs(db: AsyncSession) -> None:
    owner = await _make_user(db, "collide@example.com")

    first, _ = await organization_service.create_organization(
        db,
        name="Duplicate Name",
        owner_id=owner.id,
    )
    second, _ = await organization_service.create_organization(
        db,
        name="Duplicate Name",
        owner_id=owner.id,
    )

    assert first.slug != second.slug
    assert second.slug.startswith("duplicate-name-")

    # Both must remain individually resolvable by slug.
    assert await organization_repo.get_by_slug(db, first.slug) is not None
    assert await organization_repo.get_by_slug(db, second.slug) is not None


async def test_get_membership_returns_none_for_non_member(
    db: AsyncSession,
) -> None:
    owner = await _make_user(db, "insider@example.com")
    outsider = await _make_user(db, "outsider@example.com")

    organization, _ = await organization_service.create_organization(
        db,
        name="Closed Shop",
        owner_id=owner.id,
    )

    assert await organization_service.get_membership(
        db,
        organization.id,
        outsider.id,
    ) is None


async def test_get_membership_returns_none_for_unknown_organization(
    db: AsyncSession,
) -> None:
    user = await _make_user(db, "nowhere@example.com")

    assert await organization_service.get_membership(
        db,
        uuid.uuid4(),
        user.id,
    ) is None


async def test_list_for_user_returns_only_own_organizations(
    db: AsyncSession,
) -> None:
    owner = await _make_user(db, "mine@example.com")
    other = await _make_user(db, "theirs@example.com")

    mine, _ = await organization_service.create_organization(
        db,
        name="Mine",
        owner_id=owner.id,
    )
    await organization_service.create_organization(
        db,
        name="Theirs",
        owner_id=other.id,
    )

    rows = await organization_repo.list_for_user(db, owner.id)

    assert [organization.id for organization, _ in rows] == [mine.id]
    assert rows[0][1] == "owner"


@pytest.mark.parametrize("name", ["!!!", "株式会社"])
async def test_unsluggable_names_still_create(db: AsyncSession, name: str) -> None:
    owner = await _make_user(db, f"fallback-{abs(hash(name))}@example.com")

    organization, _ = await organization_service.create_organization(
        db,
        name=name,
        owner_id=owner.id,
    )

    assert organization.name == name
    assert organization.slug.startswith("org-")
