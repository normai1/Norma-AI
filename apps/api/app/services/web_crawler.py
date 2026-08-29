"""
Pure website-crawl logic: breadth-first over a PageFetcher, no database. The
same function backs both the initial crawl and every recrawl - only the
caller (app/services/knowledge_source.py) decides what to do with the
results.
"""

import hashlib
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.providers.web_crawler import PageFetcher, PageFetchError

MAX_PAGES_PER_CRAWL = 20
MAX_CRAWL_DEPTH = 2


@dataclass(frozen=True)
class CrawlResult:
    url: str
    extracted_text: str
    content_hash: str
    fetched_at: datetime


def _normalize_url(url: str) -> str:
    """
    Strips the fragment so "#section" variants of the same page are not
    treated as distinct pages.
    """

    return urlparse(url)._replace(fragment="").geturl()


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)

    return re.sub(r"\s+", " ", text).strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_same_host_links(
    html: str, *, base_url: str, hostname: str | None
) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []

    for anchor in soup.find_all("a", href=True):
        resolved = urljoin(base_url, anchor["href"])
        parsed = urlparse(resolved)

        if parsed.scheme not in ("http", "https"):
            continue

        if parsed.hostname != hostname:
            continue

        links.append(_normalize_url(resolved))

    return links


def _to_result(url: str, html: str) -> CrawlResult:
    text = _extract_text(html)

    return CrawlResult(
        url=url,
        extracted_text=text,
        content_hash=_content_hash(text),
        fetched_at=datetime.now(UTC),
    )


async def crawl_website(
    fetcher: PageFetcher,
    start_url: str,
    *,
    max_pages: int = MAX_PAGES_PER_CRAWL,
    max_depth: int = MAX_CRAWL_DEPTH,
) -> list[CrawlResult]:
    """
    Breadth-first crawl from start_url, same hostname only. Fetching the
    start URL is not caught here - its failure propagates to the caller,
    which maps it to the whole source failing. A failure fetching any other
    discovered page is skipped rather than fatal (a pragmatic MVP partial-
    success rule).
    """

    hostname = urlparse(start_url).hostname
    normalized_start = _normalize_url(start_url)

    start_html = await fetcher.fetch(normalized_start)

    visited = {normalized_start}
    results = [_to_result(normalized_start, start_html)]
    queue: deque[tuple[str, str, int]] = deque([(normalized_start, start_html, 0)])

    while queue and len(results) < max_pages:
        _url, html, depth = queue.popleft()

        if depth >= max_depth:
            continue

        for link in _extract_same_host_links(html, base_url=_url, hostname=hostname):
            if link in visited or len(results) >= max_pages:
                continue

            visited.add(link)

            try:
                link_html = await fetcher.fetch(link)
            except PageFetchError:
                continue

            results.append(_to_result(link, link_html))
            queue.append((link, link_html, depth + 1))

    return results
