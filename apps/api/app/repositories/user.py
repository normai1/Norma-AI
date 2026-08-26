import uuid

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
