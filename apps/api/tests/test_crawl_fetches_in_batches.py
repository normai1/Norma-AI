"""
A large crawl fetches pages a batch at a time.

It used to await each page before starting the next. At the configured
300-page ceiling that is 300 sequential round trips - minutes of a
background task doing nothing but waiting, which is how a large crawl came
to hold the API process for half an hour and then time out with nothing
written.

The opposite mistake matters as much: a site being crawled did not ask for
this traffic, and three hundred simultaneous requests at it is
indistinguishable from an attack. So: batches, not one at a time and not all
at once.
"""

import asyncio

import pytest

from app.providers.web_crawler import PageFetchError
from app.services.web_crawler import _FETCH_BATCH_SIZE, crawl_website


class _RecordingFetcher:
    """
    Records how many fetches were in flight together, so concurrency can be
    asserted rather than assumed.
    """

    def __init__(self, pages: dict[str, str], *, fail: set[str] | None = None):
        self.pages = pages
        self._fail = fail or set()
        self.in_flight = 0
        self.peak_in_flight = 0
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> str:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)

        try:
            # Yields so every fetch started in the same batch overlaps.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            self.fetched.append(url)

            if url in self._fail:
                raise PageFetchError(url)

            if url not in self.pages:
                raise PageFetchError(url)

            return self.pages[url]
        finally:
            self.in_flight -= 1


def _site(page_count: int) -> dict[str, str]:
    links = "".join(
        f'<a href="https://example.com/p{i}">p{i}</a>' for i in range(page_count)
    )
    pages = {"https://example.com": f"<html><body>Home {links}</body></html>"}

    for i in range(page_count):
        pages[f"https://example.com/p{i}"] = (
            f"<html><body><p>Page {i} about the clinic"
            "</p></body></html>"
        )

    return pages


async def test_pages_are_fetched_several_at_a_time() -> None:
    fetcher = _RecordingFetcher(_site(30))

    await crawl_website(fetcher, "https://example.com", max_pages=31, max_depth=2)

    # The whole point: more than one request in flight at once.
    assert fetcher.peak_in_flight > 1


async def test_a_crawl_never_fires_everything_at_once() -> None:
    """
    The politeness half. A hundred-page site must not become a hundred
    simultaneous requests at somebody's web server.
    """

    fetcher = _RecordingFetcher(_site(100))

    await crawl_website(fetcher, "https://example.com", max_pages=101, max_depth=2)

    assert fetcher.peak_in_flight <= _FETCH_BATCH_SIZE


async def test_every_page_is_still_crawled() -> None:
    fetcher = _RecordingFetcher(_site(20))

    results = await crawl_website(
        fetcher, "https://example.com", max_pages=21, max_depth=2
    )

    urls = {result.url for result in results}

    assert len(urls) == 21
    for i in range(20):
        assert f"https://example.com/p{i}" in urls


async def test_the_page_ceiling_is_still_respected() -> None:
    """
    Batching must not overshoot the budget: a batch of eight started when
    three pages remain would write eleven.
    """

    fetcher = _RecordingFetcher(_site(100))

    results = await crawl_website(
        fetcher, "https://example.com", max_pages=12, max_depth=2
    )

    assert len(results) == 12


async def test_one_bad_page_does_not_lose_the_batch_around_it() -> None:
    """
    A 404 in the middle of a batch costs that page and nothing else - the
    same promise the serial version made.
    """

    fetcher = _RecordingFetcher(
        _site(10), fail={"https://example.com/p3", "https://example.com/p7"}
    )

    results = await crawl_website(
        fetcher, "https://example.com", max_pages=11, max_depth=2
    )

    urls = {result.url for result in results}

    assert "https://example.com/p3" not in urls
    assert "https://example.com/p7" not in urls
    assert "https://example.com/p4" in urls
    assert len(urls) == 9


async def test_the_same_site_crawls_to_the_same_pages_in_the_same_order() -> None:
    """
    Results are zipped back onto the links that produced them in request
    order, so a crawl is reproducible however the requests raced. Without
    that, chunk ordering - and every offset stored with it - would shuffle
    between runs of the same recrawl.
    """

    first = await crawl_website(
        _RecordingFetcher(_site(25)), "https://example.com", max_pages=26, max_depth=2
    )
    second = await crawl_website(
        _RecordingFetcher(_site(25)), "https://example.com", max_pages=26, max_depth=2
    )

    assert [r.url for r in first] == [r.url for r in second]


@pytest.mark.parametrize("page_count", [0, 1, _FETCH_BATCH_SIZE - 1, _FETCH_BATCH_SIZE])
async def test_small_sites_are_unaffected(page_count: int) -> None:
    fetcher = _RecordingFetcher(_site(page_count))

    results = await crawl_website(
        fetcher, "https://example.com", max_pages=100, max_depth=2
    )

    assert len(results) == page_count + 1
