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
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from app.providers.web_crawler import PageFetcher, PageFetchError

MAX_PAGES_PER_CRAWL = 20
MAX_CRAWL_DEPTH = 2

# Where a site is expected to publish its sitemap. Only the conventional
# location is tried - reading robots.txt for a Sitemap: directive is a
# further hop this does not take.
SITEMAP_PATH = "/sitemap.xml"

# Sites are free to publish their sitemap anywhere and declare it here
# instead, which is the documented way to do it - the conventional path is
# only a convention.
ROBOTS_PATH = "/robots.txt"

# A sitemap index points at further sitemaps, and robots.txt may declare
# several; this bounds how many documents one crawl will read before giving
# up on the rest, so a site with hundreds of shards cannot turn discovery
# into its own crawl.
MAX_SITEMAP_DOCUMENTS = 10


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


def _sitemap_locations(xml: str) -> tuple[list[str], list[str]]:
    """
    The <loc> values in a sitemap document, split into (page urls, nested
    sitemap urls).

    A sitemap index nests <sitemap><loc>, while a urlset lists
    <url><loc> - the two are told apart by each <loc>'s parent, since both
    documents otherwise look alike. Namespaces are matched on local name
    only: sitemaps in the wild carry several, and some carry none.
    """

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        # Not valid XML. A site serving something unexpected at
        # /sitemap.xml (an HTML 404 page is the usual one) must not fail
        # the crawl.
        return [], []

    pages: list[str] = []
    sitemaps: list[str] = []

    for element in root.iter():
        if not element.tag.rpartition("}")[2] == "loc":
            continue

        location = (element.text or "").strip()

        if not location:
            continue

        parent_is_sitemap = any(
            child is element
            for parent in root.iter()
            if parent.tag.rpartition("}")[2] == "sitemap"
            for child in parent
        )

        if parent_is_sitemap:
            sitemaps.append(location)
        else:
            pages.append(location)

    return pages, sitemaps


def _robots_sitemap_urls(robots_txt: str) -> list[str]:
    """
    The sitemap documents a robots.txt declares.

    "Sitemap: <absolute-url>", one per line, matched case-insensitively -
    the directive is conventionally capitalised but not required to be, and
    it may appear any number of times. Every other directive is ignored:
    this reads robots.txt purely to find sitemaps, and does not interpret
    its crawl rules.
    """

    found: list[str] = []

    for line in robots_txt.splitlines():
        name, separator, value = line.partition(":")

        if not separator or name.strip().lower() != "sitemap":
            continue

        location = value.strip()

        if location and location not in found:
            found.append(location)

    return found


async def _discover_sitemap_urls(
    fetcher: PageFetcher,
    start_url: str,
    *,
    hostname: str | None,
    limit: int,
) -> list[str]:
    """
    URLs the site itself advertises - at the conventional /sitemap.xml and
    at any sitemap its robots.txt declares - following a sitemap index one
    level of documents at a time.

    Purely additive: a site with no sitemap, an unreachable one, or one
    serving junk simply contributes nothing and the crawl proceeds on
    links alone. This exists because link-following only ever reaches
    pages something else links to - a page reachable solely from a search
    box, a footer that renders client-side, or nothing at all, is invisible
    to it however deep the crawl goes.
    """

    root = urlparse(start_url)
    origin = f"{root.scheme}://{root.netloc}"
    queue: deque[str] = deque([f"{origin}{SITEMAP_PATH}"])
    seen_documents: set[str] = set()
    found: list[str] = []

    # Whatever robots.txt declares, alongside the conventional path - a
    # site that publishes its sitemap elsewhere and says so there would
    # otherwise be crawled by links alone.
    try:
        robots_txt = await fetcher.fetch(f"{origin}{ROBOTS_PATH}")
    except PageFetchError:
        robots_txt = ""

    for declared in _robots_sitemap_urls(robots_txt):
        if urlparse(declared).hostname == hostname:
            queue.append(declared)

    while queue and len(seen_documents) < MAX_SITEMAP_DOCUMENTS:
        document_url = queue.popleft()

        if document_url in seen_documents:
            continue

        seen_documents.add(document_url)

        try:
            xml = await fetcher.fetch(document_url)
        except PageFetchError:
            continue

        pages, nested = _sitemap_locations(xml)

        for nested_url in nested:
            if urlparse(nested_url).hostname == hostname:
                queue.append(nested_url)

        for page_url in pages:
            normalized = _normalize_url(page_url)

            # Same-host only, exactly as link-following is - a sitemap is
            # no more trusted to send the crawl off-domain than a link is.
            if urlparse(normalized).hostname != hostname:
                continue

            if normalized not in found:
                found.append(normalized)

            if len(found) >= limit:
                return found

    return found


async def crawl_website(
    fetcher: PageFetcher,
    start_url: str,
    *,
    max_pages: int = MAX_PAGES_PER_CRAWL,
    max_depth: int = MAX_CRAWL_DEPTH,
) -> list[CrawlResult]:
    """
    Breadth-first crawl from start_url, same hostname only, seeded with
    whatever the site advertises in its sitemap.

    Fetching the start URL is not caught here - its failure propagates to
    the caller, which maps it to the whole source failing. A failure
    fetching any other discovered page, or the sitemap itself, is skipped
    rather than fatal.
    """

    hostname = urlparse(start_url).hostname
    normalized_start = _normalize_url(start_url)

    start_html = await fetcher.fetch(normalized_start)

    visited = {normalized_start}
    results = [_to_result(normalized_start, start_html)]
    queue: deque[tuple[str, str, int]] = deque([(normalized_start, start_html, 0)])

    # Seeded at depth 0, not discovered through the link graph: a sitemap's
    # whole value is reaching pages nothing links to, so making them obey
    # the link-depth budget would discard most of what it just found. Their
    # own links are still followed from there, within max_depth.
    for sitemap_url in await _discover_sitemap_urls(
        fetcher, normalized_start, hostname=hostname, limit=max_pages
    ):
        if sitemap_url in visited or len(results) >= max_pages:
            continue

        visited.add(sitemap_url)

        try:
            sitemap_html = await fetcher.fetch(sitemap_url)
        except PageFetchError:
            continue

        results.append(_to_result(sitemap_url, sitemap_html))
        queue.append((sitemap_url, sitemap_html, 0))

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
