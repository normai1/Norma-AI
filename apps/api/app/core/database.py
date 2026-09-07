from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
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
