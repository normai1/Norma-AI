import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitRule:
    """
    How many attempts are allowed inside one fixed window.
    """

    limit: int
    window_seconds: int


# Generous enough for a person mistyping a password, far below what a
# credential-stuffing run needs.
LOGIN_RATE_LIMIT = RateLimitRule(limit=10, window_seconds=900)

REGISTER_RATE_LIMIT = RateLimitRule(limit=5, window_seconds=3600)


class RateLimitExceeded(Exception):
    """
    Raised when a caller has used up its attempts for the current window.
    """

    def __init__(self, retry_after: int) -> None:
        super().__init__("Rate limit exceeded")

        self.retry_after = retry_after


async def enforce(client: Redis, key: str, rule: RateLimitRule) -> None:
    """
    Count one attempt against a fixed window, raising once the rule is used up.

    A Redis outage lets the attempt through rather than locking every user out:
    losing the brute-force guard is the lesser failure against losing sign-in
    entirely. The dropped check is logged so the gap is visible.
    """

    namespaced = f"ratelimit:{key}"

    try:
        # One transaction, so a counter can never be left without an expiry.
        # EXPIRE ... NX only sets a TTL when the key has none, which keeps the
        # window fixed rather than sliding, and repairs a key that somehow lost
        # its expiry instead of blocking it forever.
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(namespaced)
            pipe.expire(namespaced, rule.window_seconds, nx=True)

            attempts, _ = await pipe.execute()

        if attempts > rule.limit:
            retry_after = await client.ttl(namespaced)

            raise RateLimitExceeded(
                retry_after=retry_after if retry_after > 0 else rule.window_seconds,
            )
    except RedisError:
        logger.warning("Rate limit check skipped, Redis unavailable", exc_info=True)
