from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.redis import redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    """
    Basic application health check.
    """

    return {
        "status": "ok",
        "service": "norma-clone-api",
    }


@router.get("/health/database")
async def database_health_check() -> dict:
    """
    Verify that PostgreSQL is reachable.
    """

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))

    return {
        "status": "ok",
        "database": "postgresql",
    }


@router.get("/health/redis")
async def redis_health_check() -> dict:
    """
    Verify that Redis is reachable.
    """

    response = await redis.ping()

    return {
        "status": "ok",
        "redis": response,
    }


@router.get("/health/all")
async def full_health_check() -> dict:
    """
    Verify all core infrastructure dependencies.
    """

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))

    redis_ok = await redis.ping()

    return {
        "status": "ok",
        "services": {
            "api": "ok",
            "postgresql": "ok",
            "redis": "ok" if redis_ok else "error",
        },
    }
