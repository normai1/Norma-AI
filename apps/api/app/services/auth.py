import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    EmailAlreadyRegistered,
    InactiveAccount,
    InvalidCredentials,
    InvalidRefreshToken,
)
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_token,
    normalize_email,
    verify_dummy_password,
    verify_password,
)
from app.core.tokens import create_access_token, refresh_token_expiry
from app.models.user import User
from app.repositories import session as session_repo
from app.repositories import user as user_repo


class IssuedTokens:
    """
    The token pair handed back to a client after a successful auth exchange.
    """

    def __init__(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in


async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str | None,
) -> User:
    """
    Create a new account, rejecting an address that is already registered.
    """

    normalized = normalize_email(email)

    if await user_repo.get_by_email(db, normalized) is not None:
        raise EmailAlreadyRegistered

    try:
        return await user_repo.create(
            db,
            email=normalized,
            password_hash=hash_password(password),
            full_name=full_name,
        )
    except IntegrityError as exc:
        # Two concurrent registrations can both pass the check above; the
        # unique index is the real guard.
        await db.rollback()

        raise EmailAlreadyRegistered from exc


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    """
    Verify an email/password pair and return the account behind it.
    """

    user = await user_repo.get_by_email(db, normalize_email(email))

    if user is None:
        verify_dummy_password(password)

        raise InvalidCredentials

    if not verify_password(password, user.password_hash):
        raise InvalidCredentials

    if not user.is_active:
        raise InactiveAccount

    return user


async def issue_tokens(
    db: AsyncSession,
    *,
    user: User,
    user_agent: str | None,
    ip_address: str | None,
) -> IssuedTokens:
    """
    Mint an access token and persist a matching refresh-token session.
    """

    access_token, expires_in = create_access_token(str(user.id))
    refresh_token = generate_refresh_token()

    await session_repo.create(
        db,
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=refresh_token_expiry(),
        user_agent=user_agent,
        ip_address=ip_address,
    )

    user.last_login_at = datetime.now(UTC)
    await db.flush()

    return IssuedTokens(access_token, refresh_token, expires_in)


async def refresh(
    db: AsyncSession,
    *,
    refresh_token: str,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[User, IssuedTokens]:
    """
    Exchange a refresh token for a new pair, rotating the old session out.
    """

    # Locking read: a concurrent refresh with the same token waits here and then
    # sees this one's revocation, instead of racing past the check below.
    session = await session_repo.get_by_token_hash_for_update(
        db,
        hash_token(refresh_token),
    )

    if session is None:
        raise InvalidRefreshToken

    if session.revoked_at is not None:
        # A revoked token being replayed suggests it was captured, so drop
        # every live session for that user rather than only this one.
        await session_repo.revoke_all_for_user(db, session.user_id)

        raise InvalidRefreshToken

    if session.expires_at <= datetime.now(UTC):
        raise InvalidRefreshToken

    user = await user_repo.get_by_id(db, session.user_id)

    if user is None or not user.is_active:
        raise InvalidRefreshToken

    await session_repo.revoke(db, session)

    tokens = await issue_tokens(
        db,
        user=user,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    return user, tokens


async def logout(db: AsyncSession, *, refresh_token: str) -> None:
    """
    Revoke the session behind a refresh token.

    Succeeds quietly for an unknown token so logout is never a probe for which
    tokens exist.
    """

    session = await session_repo.get_by_token_hash(db, hash_token(refresh_token))

    if session is not None:
        await session_repo.revoke(db, session)


async def get_active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """
    Load a user for a verified access token, ignoring deactivated accounts.
    """

    user = await user_repo.get_by_id(db, user_id)

    if user is None or not user.is_active:
        return None

    return user
