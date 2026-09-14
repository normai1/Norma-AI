from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    # Deliberately its own switch rather than following DEBUG, which is on
    # in every development environment. Echo logs each statement with its
    # bound parameters, and one of this application's parameters is a
    # 768-float query vector: roughly 16KB of digits, per retrieval, on the
    # per-turn path. It is then handed to item 24d's redacting formatter,
    # which scans every record for PII patterns and rewrites digit runs - so
    # the vector is not only formatted but regexed and rebuilt, thousands of
    # substitutions at a time, between the caller finishing their sentence
    # and the assistant starting to answer.
    #
    # Measured: retrieval through the internal endpoint at DEBUG=true ran
    # 1.54-1.65s against a 1.5s per-turn budget, so the turn was abandoned
    # and the assistant answered with no knowledge. See SQL_ECHO in config.
    echo=settings.sql_echo,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an async SQLAlchemy session.
    """

    async with AsyncSessionLocal() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    The application's session factory, as a dependency.

    Background work started by a request cannot use that request's session -
    it is closed once the response is sent - so it opens its own. Injecting
    the factory rather than importing AsyncSessionLocal directly is what
    lets tests point that background work at the same per-test transaction
    everything else runs in; otherwise it would open a real connection to
    the configured database and see none of the test's data.
    """

    return AsyncSessionLocal
