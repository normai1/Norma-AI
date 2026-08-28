import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

# Sentinel distinguishing "argument omitted" from an explicit None, so update()
# can tell "leave this nullable column alone" from "clear it" without callers
# needing a second signaling mechanism.
_UNSET: Any = object()


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    """
    Look up a user by an already-normalized email address.
    """

    return await db.scalar(select(User).where(User.email == email))


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """
    Look up a user by primary key.
    """

    return await db.scalar(select(User).where(User.id == user_id))


async def create(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str,
    full_name: str | None,
) -> User:
    """
    Insert a new user. The caller normalizes the email first.
    """

    user = User(
        email=email,
        password_hash=password_hash,
        full_name=full_name,
    )

    db.add(user)
    await db.flush()

    return user


async def update(
    db: AsyncSession,
    user: User,
    *,
    full_name: str | None = _UNSET,
    avatar_url: str | None = _UNSET,
) -> User:
    """
    Apply a partial update. Only full_name and avatar_url are settable here -
    an omitted argument leaves that column untouched; an explicit None clears
    it. Unlike organization/workspace update(), "leave alone" cannot be
    spelled as None, because full_name and avatar_url are themselves nullable
    columns: an explicit None is a legitimate "clear this field" request, not
    "leave it alone", so a separate sentinel marks "omitted".
    """

    if full_name is not _UNSET:
        user.full_name = full_name

    if avatar_url is not _UNSET:
        user.avatar_url = avatar_url

    await db.flush()

    return user
