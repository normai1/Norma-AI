from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import get_redis
from app.db.base import Base
from app.main import app
from app.providers.factory import get_storage_provider_dependency
from app.providers.httpx_web_crawler import get_page_fetcher_dependency
from app.providers.mock_storage import MockStorage
from app.providers.mock_web_crawler import MockPageFetcher

_REGISTER = "/api/v1/auth/register"
_ORGANIZATIONS = "/api/v1/organizations"


def _resolve_test_database_url() -> str:
    """
    Return the test database URL, refusing to run against the dev database.
    """

    url = settings.test_database_url

    if url == settings.database_url:
        raise RuntimeError(
            "TEST_DATABASE_URL matches DATABASE_URL. The test fixtures create "
            "and drop schema, so they must point at a separate database."
        )

    return url


async def _ensure_database_exists(url: str) -> None:
    """
    Create the test database if it is not there yet.
    """

    admin_url, _, database_name = url.rpartition("/")

    engine = create_async_engine(
        f"{admin_url}/postgres",
        isolation_level="AUTOCOMMIT",
    )

    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            )

            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncGenerator:
    """
    Session-wide engine bound to a freshly created test schema.
    """

    url = _resolve_test_database_url()

    await _ensure_database_exists(url)

    test_engine = create_async_engine(url)

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield test_engine

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)

    await test_engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def connection(engine) -> AsyncGenerator[AsyncConnection, None]:
    """
    Per-test connection wrapped in a transaction that is always rolled back.
    """

    async with engine.connect() as conn:
        transaction = await conn.begin()

        yield conn

        await transaction.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def db(connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """
    Async session bound to the rolled-back per-test transaction.
    """

    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(loop_scope="session")
async def redis_client() -> AsyncGenerator[Redis, None]:
    """
    Redis bound to a throwaway database index, emptied around each test.
    """

    if settings.test_redis_url == settings.redis_url:
        raise RuntimeError(
            "TEST_REDIS_URL matches REDIS_URL. The test fixtures flush the "
            "database, so they must point at a separate index."
        )

    client = Redis.from_url(settings.test_redis_url, decode_responses=True)

    await client.flushdb()

    yield client

    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture(loop_scope="session")
async def storage() -> MockStorage:
    """
    The in-memory storage double a test can inspect directly to prove a real
    upload/download round-trip happened, not just a DB write.
    """

    return MockStorage()


@pytest_asyncio.fixture(loop_scope="session")
async def page_fetcher() -> MockPageFetcher:
    """
    The scripted page fetcher a test populates with its own URL -> HTML
    graph, injected in place of a real network fetch.
    """

    return MockPageFetcher()


@pytest_asyncio.fixture(loop_scope="session")
async def client(
    db: AsyncSession,
    redis_client: Redis,
    storage: MockStorage,
    page_fetcher: MockPageFetcher,
) -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP client whose requests run inside the per-test transaction.

    A route's `db.commit()` only commits the savepoint the session is bound to,
    so writes stay visible to the test and still roll back afterwards.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    async def override_get_redis() -> Redis:
        return redis_client

    def override_get_storage_provider() -> MockStorage:
        return storage

    def override_get_page_fetcher() -> MockPageFetcher:
        return page_fetcher

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_storage_provider_dependency] = (
        override_get_storage_provider
    )
    app.dependency_overrides[get_page_fetcher_dependency] = override_get_page_fetcher

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http_client:
        yield http_client

    app.dependency_overrides.clear()


async def _signed_in(client: AsyncClient, email: str) -> dict[str, str]:
    """
    Register a user and return an Authorization header for them.
    """

    response = await client.post(
        _REGISTER,
        json={"email": email, "password": "a-strong-password"},
    )

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _org_with_owner(
    client: AsyncClient,
    email: str,
    name: str = "Test Org",
) -> tuple[dict[str, str], str]:
    """
    Register a user and create an organization they own.

    Returns the owner's auth headers and the organization id.
    """

    headers = await _signed_in(client, email)
    created = await client.post(_ORGANIZATIONS, json={"name": name}, headers=headers)

    return headers, created.json()["id"]
