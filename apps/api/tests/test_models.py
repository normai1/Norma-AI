from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, hash_token
from app.models.session import UserSession
from app.models.user import User


async def _make_user(db: AsyncSession, email: str = "person@example.com") -> User:
    user = User(
        email=email,
        password_hash=hash_password("a-real-password"),
        full_name="Test Person",
    )

    db.add(user)
    await db.flush()

    return user


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
