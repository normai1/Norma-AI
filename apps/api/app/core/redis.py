from redis.asyncio import Redis

from app.core.config import settings

redis = Redis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> Redis:
    """
    Return the application Redis client.
    """

    return redis
