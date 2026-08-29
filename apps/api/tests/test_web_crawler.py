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
