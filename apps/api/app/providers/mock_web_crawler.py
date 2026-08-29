"""
Deterministic page-fetcher mock. No network - the test suite's actual
fetcher, injected via app.dependency_overrides the same way MockStorage is.
"""

from app.providers.web_crawler import PageFetchError


class MockPageFetcher:
    """
    Returns caller-scripted HTML for a fixed set of URLs. Fetching an
    unregistered URL raises PageFetchError, matching a real fetcher's
    behavior for a page that doesn't exist.
    """

    def __init__(self, pages: dict[str, str] | None = None) -> None:
        # Public and mutable - a test populates this after construction the
        # same way MockStorage.objects is inspected/seeded directly.
        self.pages: dict[str, str] = dict(pages or {})

    async def fetch(self, url: str) -> str:
        if url not in self.pages:
            raise PageFetchError(f"No scripted page for {url!r}")

        return self.pages[url]
