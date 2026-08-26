"""
Proof that the database-level guards in feature 2 actually hold.

Both guards here are invisible to the rest of the suite: deleting
`.with_for_update()` or dropping the partial index leaves every other test
green. These tests exist so a future refactor cannot quietly remove them, and
they follow the pattern `test_auth_concurrency.py` established in feature 1 -
each locking assertion is paired with a non-locking discriminator, so a test
that stopped exercising the lock would fail rather than pass vacuously.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import InvitationConflict
from app.core.security import hash_password
from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.models.user import User
from app.repositories import invitation as invitation_repo
from app.repositories import organization_member as member_repo
from app.services import invitation as invitation_service

# Long enough that a genuinely blocked query cannot finish inside it, short
# enough that a non-blocking read comfortably does.
BLOCK_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def _seeded_org(engine) -> AsyncIterator[tuple[async_sessionmaker, Organization]]:
    """
    Commit an organization with two owners, then clean it up.

    The rows must really be committed for a second transaction to contend for
    them, so this sidesteps the rolled-back `db` fixture.
    """

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with factory() as setup:
        organization = Organization(name="Contended", slug="contended-org")
        setup.add(organization)
        await setup.flush()

        for index in range(2):
            user = User(
                email=f"contended{index}@example.com",
                password_hash=hash_password("a-strong-password"),
            )
            setup.add(user)
            await setup.flush()

            setup.add(
                OrganizationMember(
                    organization_id=organization.id,
                    user_id=user.id,
                    role="owner",
                )
            )

        await setup.commit()
        organization_id = organization.id

    try:
        yield factory, organization
    finally:
        async with factory() as cleanup:
            await cleanup.execute(
                delete(Organization).where(Organization.id == organization_id),
            )
            await cleanup.execute(
                delete(User).where(User.email.like("contended%@example.com")),
            )
            await cleanup.commit()


async def test_owner_count_blocks_a_second_reader(engine) -> None:
    """
    The lock the last-owner guard depends on must make a second caller wait.
    """

    async with _seeded_org(engine) as (factory, organization):
        async with factory() as holder:
            held = await member_repo.count_by_role_for_update(
                holder,
                organization.id,
                "owner",
            )

            assert held == 2

            async with factory() as contender:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        member_repo.count_by_role_for_update(
                            contender,
                            organization.id,
                            "owner",
                        ),
                        timeout=BLOCK_TIMEOUT_SECONDS,
                    )

                await contender.rollback()

            await holder.rollback()


async def test_a_plain_read_of_the_same_rows_does_not_block(engine) -> None:
    """
    The discriminator for the test above.

    A non-locking read sails past a held row lock under MVCC, which is exactly
    why counting owners without `FOR UPDATE` let two demotions both proceed. If
    this ever started timing out, the test above would prove nothing.
    """

    async with _seeded_org(engine) as (factory, organization):
        async with factory() as holder:
            await member_repo.count_by_role_for_update(
                holder,
                organization.id,
                "owner",
            )

            async with factory() as reader:
                owners = await asyncio.wait_for(
                    reader.scalars(
                        select(OrganizationMember).where(
                            OrganizationMember.organization_id == organization.id,
                            OrganizationMember.role == "owner",
                        ),
                    ),
                    timeout=BLOCK_TIMEOUT_SECONDS,
                )

                assert len(owners.all()) == 2

                await reader.rollback()

            await holder.rollback()


async def test_a_second_pending_invitation_is_rejected(db: AsyncSession) -> None:
    """
    The partial unique index, not application code, is what enforces this.
    """

    organization = Organization(name="One Invite", slug="one-invite-only")
    db.add(organization)
    await db.flush()

    def _invitation(token_hash: str) -> Invitation:
        return Invitation(
            organization_id=organization.id,
            email="only-one@example.com",
            role="member",
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=14),
            status="pending",
        )

    db.add(_invitation("a" * 64))
    await db.flush()

    db.add(_invitation("b" * 64))

    with pytest.raises(IntegrityError):
        await db.flush()


async def test_the_index_only_constrains_pending_invitations(
    db: AsyncSession,
) -> None:
    """
    Discriminator: the index is partial, so a superseded invitation must not
    block a fresh one for the same address.
    """

    organization = Organization(name="Reinvite", slug="reinvite-allowed")
    db.add(organization)
    await db.flush()

    revoked = Invitation(
        organization_id=organization.id,
        email="again@example.com",
        role="member",
        token_hash="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(days=14),
        status="revoked",
    )
    db.add(revoked)
    await db.flush()

    db.add(
        Invitation(
            organization_id=organization.id,
            email="again@example.com",
            role="member",
            token_hash="d" * 64,
            expires_at=datetime.now(UTC) + timedelta(days=14),
            status="pending",
        )
    )

    # No IntegrityError: only one of the two rows is pending.
    await db.flush()


class _NullSender:
    """Stands in for the email sender; delivery is not what these tests cover."""

    async def send(self, **kwargs) -> None:
        return None


async def _org_with_owner(db: AsyncSession, slug: str) -> OrganizationMember:
    organization = Organization(name=slug, slug=slug)
    db.add(organization)
    await db.flush()

    user = User(
        email=f"{slug}@example.com",
        password_hash=hash_password("a-strong-password"),
    )
    db.add(user)
    await db.flush()

    member = OrganizationMember(
        organization_id=organization.id,
        user_id=user.id,
        role="owner",
    )
    db.add(member)
    await db.flush()

    return member


async def test_exhausted_invite_retries_raise_a_domain_error(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Callers must see InvitationConflict, which the route maps to 409.

    Regression: the retry loop used to re-raise the underlying IntegrityError on
    its final attempt, so the domain error and the route's 409 handler were both
    unreachable and a persistent collision surfaced as a 500.
    """

    inviter = await _org_with_owner(db, "exhausted-retries")

    async def always_collide(*args, **kwargs):
        raise IntegrityError("forced collision", None, Exception("forced"))

    monkeypatch.setattr(invitation_repo, "create", always_collide)

    with pytest.raises(InvitationConflict):
        await invitation_service.invite(
            db,
            organization_id=inviter.organization_id,
            email="never-lands@example.com",
            role="member",
            inviter=inviter,
            sender=_NullSender(),
        )


async def test_a_single_collision_still_succeeds(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Discriminator: the retry must absorb a transient collision rather than
    turning every IntegrityError into a conflict.
    """

    inviter = await _org_with_owner(db, "one-collision")

    real_create = invitation_repo.create
    calls = {"n": 0}

    async def collide_once(*args, **kwargs):
        calls["n"] += 1

        if calls["n"] == 1:
            raise IntegrityError("forced collision", None, Exception("forced"))

        return await real_create(*args, **kwargs)

    monkeypatch.setattr(invitation_repo, "create", collide_once)

    invitation, token = await invitation_service.invite(
        db,
        organization_id=inviter.organization_id,
        email="lands-on-retry@example.com",
        role="member",
        inviter=inviter,
        sender=_NullSender(),
    )

    assert calls["n"] == 2
    assert invitation.status == "pending"
    assert token
