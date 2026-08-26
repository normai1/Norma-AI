from fastapi import Request

from app.core.config import settings

MAX_USER_AGENT_LENGTH = 512


def client_user_agent(request: Request) -> str | None:
    """
    Return the caller's user agent, trimmed to the column width.
    """

    user_agent = request.headers.get("user-agent")

    return user_agent[:MAX_USER_AGENT_LENGTH] if user_agent else None


def client_ip(request: Request) -> str | None:
    """
    Return the caller's address, seeing through configured trusted proxies.

    `X-Forwarded-For` grows left to right, each proxy appending the address it
    received from, so the trustworthy entries are the rightmost ones. Anything a
    client sends itself lands on the left and is ignored. Counting hops from the
    right is therefore what makes the result unspoofable; taking the leftmost
    entry would let any caller choose its own identity and, since that identity
    keys the rate limiter, opt out of rate limiting entirely.
    """

    peer = request.client.host if request.client else None
    hop_count = settings.trusted_proxy_count

    if hop_count <= 0:
        return peer

    forwarded = request.headers.get("x-forwarded-for")

    if not forwarded:
        return peer

    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    index = len(hops) - hop_count

    # Fewer hops than configured proxies means the request did not arrive
    # through the expected chain, so the header cannot be trusted.
    if index < 0 or index >= len(hops):
        return peer

    return hops[index]
