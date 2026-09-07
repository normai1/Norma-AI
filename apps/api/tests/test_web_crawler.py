import pytest

from app.providers.httpx_web_crawler import HttpxPageFetcher
from app.providers.mock_web_crawler import MockPageFetcher
from app.providers.web_crawler import PageFetchError, UnsafeUrlRejected
from app.services.web_crawler import crawl_website


async def test_mock_page_fetcher_returns_scripted_html() -> None:
    fetcher = MockPageFetcher({"http://example.com/": "<html>hi</html>"})

    assert await fetcher.fetch("http://example.com/") == "<html>hi</html>"


async def test_mock_page_fetcher_raises_for_an_unregistered_url() -> None:
    fetcher = MockPageFetcher()

    with pytest.raises(PageFetchError):
        await fetcher.fetch("http://example.com/missing")


async def test_httpx_fetcher_rejects_a_loopback_address(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.providers.httpx_web_crawler._resolve_addresses",
        lambda hostname: _async_result(["127.0.0.1"]),
    )

    with pytest.raises(UnsafeUrlRejected):
        await HttpxPageFetcher().fetch("http://internal.example/")


async def test_httpx_fetcher_rejects_a_private_address(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.providers.httpx_web_crawler._resolve_addresses",
        lambda hostname: _async_result(["10.0.0.5"]),
    )

    with pytest.raises(UnsafeUrlRejected):
        await HttpxPageFetcher().fetch("http://internal.example/")


async def test_httpx_fetcher_rejects_a_link_local_address(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.providers.httpx_web_crawler._resolve_addresses",
        lambda hostname: _async_result(["169.254.169.254"]),
    )

    with pytest.raises(UnsafeUrlRejected):
        await HttpxPageFetcher().fetch("http://metadata.example/")


async def test_httpx_fetcher_rejects_an_unsupported_scheme() -> None:
    with pytest.raises(UnsafeUrlRejected):
        await HttpxPageFetcher().fetch("ftp://example.com/")


async def _async_result(value):
    return value


def _page(body: str) -> str:
    return f"<html><body>{body}</body></html>"


async def test_crawl_discovers_a_single_page_with_no_links() -> None:
    fetcher = MockPageFetcher({"http://example.com/": _page("Hello, world.")})

    results = await crawl_website(fetcher, "http://example.com/")

    assert len(results) == 1
    assert results[0].url == "http://example.com/"
    assert results[0].extracted_text == "Hello, world."


async def test_crawl_follows_same_hostname_links() -> None:
    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page('<a href="/about">About</a>'),
            "http://example.com/about": _page("About us."),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    urls = {result.url for result in results}
    assert urls == {"http://example.com/", "http://example.com/about"}


async def test_crawl_ignores_external_domain_links() -> None:
    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page('<a href="http://other.example/">Other</a>'),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    assert len(results) == 1
    assert results[0].url == "http://example.com/"


async def test_crawl_propagates_a_root_fetch_failure() -> None:
    fetcher = MockPageFetcher()

    with pytest.raises(PageFetchError):
        await crawl_website(fetcher, "http://example.com/")


async def test_crawl_skips_a_failing_sub_page_without_failing_the_whole_crawl() -> None:
    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page(
                '<a href="/broken">Broken</a><a href="/ok">Ok</a>'
            ),
            "http://example.com/ok": _page("This page works."),
            # "/broken" deliberately not registered - fetch fails for it.
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    urls = {result.url for result in results}
    assert urls == {"http://example.com/", "http://example.com/ok"}


async def test_crawl_enforces_the_page_count_cap() -> None:
    pages = {
        "http://example.com/": _page(
            "<br>".join(f'<a href="/page{i}">p{i}</a>' for i in range(30))
        )
    }
    for i in range(30):
        pages[f"http://example.com/page{i}"] = _page(f"Page {i}.")

    fetcher = MockPageFetcher(pages)

    results = await crawl_website(fetcher, "http://example.com/", max_pages=20)

    assert len(results) == 20


async def test_crawl_enforces_the_depth_cap() -> None:
    # A straight chain: root -> a -> b -> c -> d. depth=2 should reach the
    # root, "a" (depth 1), and "b" (depth 2), but never expand "b"'s own
    # link to "c" (that would be depth 3).
    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page('<a href="/a">a</a>'),
            "http://example.com/a": _page('<a href="/b">b</a>'),
            "http://example.com/b": _page('<a href="/c">c</a>'),
            "http://example.com/c": _page('<a href="/d">d</a>'),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/", max_depth=2)

    urls = {result.url for result in results}
    assert urls == {
        "http://example.com/",
        "http://example.com/a",
        "http://example.com/b",
    }


async def test_crawl_content_hash_is_deterministic_for_the_same_text() -> None:
    fetcher = MockPageFetcher({"http://example.com/": _page("Same content.")})

    first = await crawl_website(fetcher, "http://example.com/")
    second = await crawl_website(fetcher, "http://example.com/")

    assert first[0].content_hash == second[0].content_hash


def _sitemap(*urls: str) -> str:
    entries = "".join(f"<url><loc>{url}</loc></url>" for url in urls)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


async def test_crawl_reaches_a_page_only_the_sitemap_knows_about() -> None:
    """
    The whole point of reading a sitemap: link-following can only ever
    reach pages something links to, so a page nothing links to is invisible
    to it at any depth.
    """

    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page("Homepage with no links at all."),
            "http://example.com/sitemap.xml": _sitemap(
                "http://example.com/", "http://example.com/orphan"
            ),
            "http://example.com/orphan": _page("Nothing links here."),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    assert {r.url for r in results} == {
        "http://example.com/",
        "http://example.com/orphan",
    }


async def test_crawl_follows_a_sitemap_index_to_its_child_sitemaps() -> None:
    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page("Homepage."),
            "http://example.com/sitemap.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<sitemap><loc>http://example.com/sitemap-1.xml</loc></sitemap>"
                "</sitemapindex>"
            ),
            "http://example.com/sitemap-1.xml": _sitemap("http://example.com/deep"),
            "http://example.com/deep": _page("Listed in a child sitemap."),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    assert "http://example.com/deep" in {r.url for r in results}


async def test_crawl_ignores_sitemap_entries_on_another_host() -> None:
    """
    A sitemap is no more trusted to send the crawl off-domain than a link
    is - the source belongs to one site.
    """

    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page("Homepage."),
            "http://example.com/sitemap.xml": _sitemap("http://elsewhere.com/page"),
            "http://elsewhere.com/page": _page("Someone else's page."),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    assert {r.url for r in results} == {"http://example.com/"}


async def test_crawl_survives_a_missing_or_junk_sitemap() -> None:
    """
    Most sites have no sitemap, and plenty serve an HTML 404 page at that
    path. Neither may cost the crawl anything.
    """

    without = MockPageFetcher(
        {"http://example.com/": _page('<a href="/about">About</a>')}
    )
    without.pages["http://example.com/about"] = _page("About us.")

    junk = MockPageFetcher(dict(without.pages))
    junk.pages["http://example.com/sitemap.xml"] = "<html><body>Not found</body></html>"

    assert {r.url for r in await crawl_website(without, "http://example.com/")} == {
        "http://example.com/",
        "http://example.com/about",
    }
    assert {r.url for r in await crawl_website(junk, "http://example.com/")} == {
        "http://example.com/",
        "http://example.com/about",
    }


async def test_sitemap_pages_still_respect_the_page_cap() -> None:
    pages = {"http://example.com/": _page("Homepage.")}
    listed = [f"http://example.com/p{i}" for i in range(30)]

    for url in listed:
        pages[url] = _page("A page.")

    pages["http://example.com/sitemap.xml"] = _sitemap(*listed)

    results = await crawl_website(
        MockPageFetcher(pages), "http://example.com/", max_pages=5
    )

    assert len(results) == 5


def _robots(*lines: str) -> str:
    return "\n".join(lines) + "\n"


async def test_crawl_uses_a_sitemap_declared_only_in_robots_txt() -> None:
    """
    A site is free to publish its sitemap anywhere and declare it in
    robots.txt - the conventional path is only a convention, and a site
    doing this would otherwise be crawled by links alone.
    """

    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page("Homepage with no links."),
            "http://example.com/robots.txt": _robots(
                "User-agent: *",
                "Allow: /",
                "Sitemap: http://example.com/custom/sitemap-a.xml",
            ),
            "http://example.com/custom/sitemap-a.xml": _sitemap(
                "http://example.com/hidden"
            ),
            "http://example.com/hidden": _page("Only robots.txt points here."),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    assert "http://example.com/hidden" in {r.url for r in results}


async def test_crawl_reads_every_sitemap_robots_txt_declares() -> None:
    """
    The directive may appear any number of times, and is matched
    case-insensitively - it is conventionally capitalised but not required
    to be.
    """

    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page("Homepage."),
            "http://example.com/robots.txt": _robots(
                "Sitemap: http://example.com/one.xml",
                "sitemap: http://example.com/two.xml",
            ),
            "http://example.com/one.xml": _sitemap("http://example.com/first"),
            "http://example.com/two.xml": _sitemap("http://example.com/second"),
            "http://example.com/first": _page("First."),
            "http://example.com/second": _page("Second."),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    assert {"http://example.com/first", "http://example.com/second"} <= {
        r.url for r in results
    }


async def test_crawl_ignores_a_robots_sitemap_on_another_host() -> None:
    fetcher = MockPageFetcher(
        {
            "http://example.com/": _page("Homepage."),
            "http://example.com/robots.txt": _robots(
                "Sitemap: http://elsewhere.com/sitemap.xml",
            ),
            "http://elsewhere.com/sitemap.xml": _sitemap("http://elsewhere.com/page"),
            "http://elsewhere.com/page": _page("Someone else's page."),
        }
    )

    results = await crawl_website(fetcher, "http://example.com/")

    assert {r.url for r in results} == {"http://example.com/"}
