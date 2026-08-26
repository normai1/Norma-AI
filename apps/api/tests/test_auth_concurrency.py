import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password, hash_token
from app.models.session import UserSession
from app.models.user import User
from app.repositories import session as session_repo

TOKEN = "a-contended-refresh-token"
EMAIL = "race@example.com"

# Long enough that a genuinely blocked query cannot finish inside it, short
# enough that a non-blocking read comfortably does.
BLOCK_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def _seeded_session(engine) -> AsyncIterator[async_sessionmaker]:
    """
    Commit a user and one refresh session, then clean both up afterwards.

    These rows must really be committed for a second transaction to contend for
    them, so this deliberately sidesteps the rolled-back `db` fixture.
    """

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with factory() as setup:
        user = User(email=EMAIL, password_hash=hash_password("a-strong-password"))
        setup.add(user)
        await setup.flush()

        setup.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_token(TOKEN),
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )

        await setup.commit()
        user_id = user.id

    try:
        yield factory
    finally:
        async with factory() as cleanup:
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()


async def test_locking_read_blocks_a_second_reader(engine) -> None:
    """
    The lock rotation relies on must actually make a second reader wait.
    """

    async with _seeded_session(engine) as factory:
        async with factory() as holder:
            held = await session_repo.get_by_token_hash_for_update(
                holder,
                hash_token(TOKEN),
            )

            assert held is not None

            async with factory() as contender:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        session_repo.get_by_token_hash_for_update(
                            contender,
                            hash_token(TOKEN),
                        ),
                        timeout=BLOCK_TIMEOUT_SECONDS,
                    )

                await contender.rollback()

            await holder.rollback()


async def test_plain_read_does_not_block(engine) -> None:
    """
    The discriminator for the test above.

    A plain read sails past a held row lock under MVCC, which is exactly why
    rotation reading without `FOR UPDATE` let two requests both see an
    unrevoked session. If this test ever started timing out, the one above
    would be proving nothing.
    """

    async with _seeded_session(engine) as factory:
        async with factory() as holder:
            await session_repo.get_by_token_hash_for_update(
                holder,
                hash_token(TOKEN),
            )

            async with factory() as reader:
                found = await asyncio.wait_for(
                    session_repo.get_by_token_hash(reader, hash_token(TOKEN)),
                    timeout=BLOCK_TIMEOUT_SECONDS,
                )

                assert found is not None
                assert found.revoked_at is None

                await reader.rollback()

            await holder.rollback()
