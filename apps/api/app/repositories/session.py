import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import UserSession


async def create(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None,
    ip_address: str | None,
) -> UserSession:
    """
    Record a newly issued refresh token.
    """

    session = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    db.add(session)
    await db.flush()

    return session


async def get_by_token_hash(db: AsyncSession, token_hash: str) -> UserSession | None:
    """
    Find a session by the hash of its refresh token.
    """

    return await db.scalar(
        select(UserSession).where(UserSession.token_hash == token_hash),
    )


async def get_by_token_hash_for_update(
    db: AsyncSession,
    token_hash: str,
) -> UserSession | None:
    """
    Find a session and hold a row lock on it for the rest of the transaction.

    Rotation reads the session, decides whether it is still usable, and then
    revokes it. Without the lock those steps interleave, and two requests
    carrying the same refresh token can both pass the revocation check.
    """

    return await db.scalar(
        select(UserSession)
        .where(UserSession.token_hash == token_hash)
        .with_for_update(),
    )


async def revoke(db: AsyncSession, session: UserSession) -> None:
    """
    Mark one session as no longer usable.
    """

    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)

    await db.flush()


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    """
    Revoke every live session for a user.

    Used when a already-revoked refresh token is replayed, which suggests the
    token was captured, so every session for that user is dropped.
    """

    await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )

    await db.flush()
