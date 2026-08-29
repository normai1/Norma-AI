"""
The real PageFetcher, used in every environment (there is no mock/real
choice to make here - see web_crawler.py's module docstring).
"""

import asyncio
import ipaddress
from urllib.parse import urlparse

import httpx

from app.providers.web_crawler import (
    PageFetchError,
    PageFetchTimeout,
    UnsafeUrlRejected,
)

FETCH_TIMEOUT_SECONDS = 10.0


async def _resolve_addresses(hostname: str) -> list[str]:
    """
    Resolve hostname to its IP addresses. A separate function so a test can
    monkeypatch just the resolution step, without a real DNS lookup or
    reaching into asyncio's event loop internals.
    """

    try:
        results = await asyncio.get_running_loop().getaddrinfo(hostname, None)
    except OSError as exc:
        raise UnsafeUrlRejected(f"Could not resolve {hostname!r}") from exc

    return [sockaddr[0] for _, _, _, _, sockaddr in results]


async def _assert_safe_host(url: str) -> None:
    """
    Resolve url's hostname and reject it if any resolved address is
    private, loopback, or link-local. Applied before every fetch - the
    start URL and every discovered link - since the caller supplies a URL
    the server then fetches (a classic SSRF vector if unguarded).
    """

    hostname = urlparse(url).hostname

    if not hostname:
        raise UnsafeUrlRejected(f"{url!r} has no hostname")

    for raw_address in await _resolve_addresses(hostname):
        address = ipaddress.ip_address(raw_address)

        if address.is_private or address.is_loopback or address.is_link_local:
            raise UnsafeUrlRejected(
                f"{hostname!r} resolves to a non-public address ({address})",
            )


class HttpxPageFetcher:
    """
    Fetches a page over HTTP(S). Never follows redirects automatically - a
    redirect response is treated as a fetch failure, not silently followed
    into an unvalidated target.
    """

    async def fetch(self, url: str) -> str:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            raise UnsafeUrlRejected(f"Unsupported scheme in {url!r}")

        await _assert_safe_host(url)

        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                response = await client.get(url, timeout=FETCH_TIMEOUT_SECONDS)
        except httpx.TimeoutException as exc:
            raise PageFetchTimeout(f"Timed out fetching {url!r}") from exc
        except httpx.TransportError as exc:
            raise PageFetchError(f"Could not fetch {url!r}") from exc

        if response.is_redirect or not response.is_success:
            raise PageFetchError(
                f"Fetching {url!r} returned status {response.status_code}",
            )

        return response.text


def get_page_fetcher_dependency() -> HttpxPageFetcher:
    """
    FastAPI dependency entry point for the page fetcher.
    """

    return HttpxPageFetcher()
