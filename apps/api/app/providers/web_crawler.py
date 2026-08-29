"""
Page fetcher contract for website ingestion. There is exactly one real way
to fetch a web page - unlike speech/storage, there is no production
"provider choice" here, only the test-double half of that pattern
(HttpxPageFetcher is always used for real; tests inject MockPageFetcher).
"""

from typing import Protocol


class PageFetchError(Exception):
    """
    Base class for a page fetch's own failures, distinct from a bug in the
    calling code.
    """


class PageFetchTimeout(PageFetchError):
    """
    The page did not respond within the fetch timeout.
    """


class UnsafeUrlRejected(PageFetchError):
    """
    The URL's hostname resolves to a private, loopback, or link-local
    address - refused before any request is made. The caller supplies a URL
    the server then fetches, so an unguarded fetch is a classic SSRF vector.
    """


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> str:
        """
        Return the raw HTML at url. Raises PageFetchError (or a subclass)
        if the page cannot be fetched - including a redirect response,
        which is treated as a failure rather than followed.
        """
        ...
