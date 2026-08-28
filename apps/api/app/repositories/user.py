import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


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


async def update(db: AsyncSession, user: User, **fields: Any) -> User:
    """
    Apply a partial update from already-filtered fields - only the keys the
    caller wants to change. Unlike organization/workspace update(), an omitted
    field is left alone by omitting it from the call, not by passing None:
    full_name and avatar_url are nullable columns, so an explicit None is a
    legitimate "clear this field" request, not "leave it alone".
    """

    for key, value in fields.items():
        setattr(user, key, value)

    await db.flush()

    return user
