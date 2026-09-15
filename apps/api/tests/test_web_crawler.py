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


def test_page_furniture_is_not_treated_as_knowledge() -> None:
    """
    A menu, a footer and a form's labels appear on every page of a site, so
    leaving them in means every chunk carries the same noise.
    """

    from app.services.web_crawler import _extract_text

    html = """
        <html><body>
          <nav>Home Pricing Contact</nav>
          <header>Renate AI</header>
          <p>We interview candidates by phone in English and Hindi.</p>
          <form><label>Phone Number</label>
            <input value="+91 1234123400"></form>
          <button>SUBMIT</button>
          <footer>All rights reserved</footer>
        </body></html>
    """

    text = _extract_text(html)

    assert "interview candidates by phone" in text
    for furniture in (
        "Home Pricing Contact",
        "SUBMIT",
        "All rights reserved",
        "Phone Number",
    ):
        assert furniture not in text


def test_explicitly_decorative_content_is_dropped() -> None:
    from app.services.web_crawler import _extract_text

    html = (
        '<html><body><span aria-hidden="true">icon</span>'
        "<p>Real content.</p></body></html>"
    )

    text = _extract_text(html)

    assert text == "Real content."


def test_content_inside_a_hidden_wrapper_is_kept() -> None:
    """
    Regression, measured against a real site: framework-rendered pages
    server-render their whole body inside a wrapper carrying the hidden
    attribute and reveal it with script. Dropping [hidden] kept 37 of 1,751
    characters - the page, gone.
    """

    from app.services.web_crawler import _extract_text

    html = (
        "<html><body><div hidden>"
        "<p>The clinic opens at nine.</p></div></body></html>"
    )

    assert "The clinic opens at nine." in _extract_text(html)


def test_ordinary_page_content_survives_with_its_structure() -> None:
    """
    A heading and a paragraph come out as separate lines, not one run of
    words.

    This used to flatten to "Pricing Lite is 50 interviews a month.", and so
    did every page: measured on a real crawl, the pricing page was 5,668
    characters containing zero newlines. The chunker splits on paragraph
    breaks first and line breaks second, so a page with neither gives it
    nothing to split on and it cuts wherever the token budget lands -
    producing chunks that begin and end mid-thought.
    """

    from app.services.web_crawler import _extract_text

    html = (
        "<html><body><h1>Pricing</h1>"
        "<p>Lite is 50 interviews a month.</p></body></html>"
    )

    assert _extract_text(html) == "Pricing\nLite is 50 interviews a month."


def test_a_sentence_is_not_broken_up_by_the_markup_inside_it() -> None:
    """
    Why block-level tags get the line break rather than get_text's separator:
    a separator breaks between every text node, so an anchor or a bold run
    inside a sentence would tear the sentence in half - trading one chunking
    problem for a worse one.
    """

    from app.services.web_crawler import _extract_text

    html = (
        "<html><body><p>Call <a href='/x'>our team</a> on "
        "<strong>9am</strong> weekdays.</p></body></html>"
    )

    assert _extract_text(html) == "Call our team on 9am weekdays."


def test_list_items_become_their_own_lines() -> None:
    """
    What lets a pricing table chunk correctly: each plan and the price beside
    it stay together, instead of the plan names landing in one chunk and the
    prices in the next.
    """

    from app.services.web_crawler import _extract_text

    html = (
        "<html><body><ul><li>Hobby - Free</li>"
        "<li>Individual - $20 a month</li></ul></body></html>"
    )

    assert _extract_text(html) == "Hobby - Free\nIndividual - $20 a month"


def test_a_cloudflare_obfuscated_email_is_decoded() -> None:
    """
    Reported from a real call: the assistant told a caller to write to
    "[email protected]".

    Cloudflare's Email Address Obfuscation replaces every mailto with that
    literal text and hides the address in a data-cfemail attribute for its
    own script to decode in the browser. A crawler runs no script, so the
    placeholder is what reached the knowledge base while the real address -
    support@renate.in - sat in the attribute on the same page.
    """

    from app.services.web_crawler import _extract_text

    html = (
        '<p>Email <a href="/cdn-cgi/l/email-protection" class="__cf_email__" '
        'data-cfemail="dba8aeababb4a9af9ba9beb5baafbef5b2b5">'
        "[email&#160;protected]</a> today.</p>"
    )

    text = _extract_text(html)

    assert "support@renate.in" in text
    assert "[email" not in text


def test_the_address_is_recovered_from_the_link_too() -> None:
    """
    The same address is encoded in the href Cloudflare leaves behind, which
    is what remains when the element carrying the attribute is not matched.
    """

    from app.services.web_crawler import _extract_text

    html = (
        '<p>Write to <a href="/cdn-cgi/l/email-protection#'
        'dba8aeababb4a9af9ba9beb5baafbef5b2b5">here</a>.</p>'
    )

    assert "support@renate.in" in _extract_text(html)


def test_a_malformed_encoding_is_left_alone_rather_than_decoded_to_nonsense() -> None:
    """
    Degrading to the placeholder is bad; writing decoded rubbish into the
    knowledge base as an address would be worse.
    """

    from app.services.web_crawler import _decode_cloudflare_email

    assert _decode_cloudflare_email("zzzz") is None
    assert _decode_cloudflare_email("") is None
    # Decodes cleanly, but is not an address.
    assert _decode_cloudflare_email("dba8ae") is None


def test_an_ordinary_email_on_a_page_is_untouched() -> None:
    from app.services.web_crawler import _extract_text

    assert "hello@example.com" in _extract_text("<p>Email hello@example.com.</p>")


def test_a_pages_label_comes_from_its_url_not_its_title() -> None:
    """
    The title looked like the obvious source and is not: sites disagree about
    what order it goes in. Measured on one real site in a single crawl,
    "Cursor - Pricing" puts the site name first and "Overview | Cursor Docs"
    puts it last, so splitting on the separator yields the site's own name
    for the pricing page - on every chunk of every page, saying nothing about
    any of them.
    """

    from app.services.web_crawler import _extract_title

    html = "<html><head><title>Acme - Pricing</title></head><body>x</body></html>"

    assert _extract_title(html, url="https://acme.com/pricing") == "Pricing"


def test_the_url_carries_context_the_title_drops() -> None:
    """
    A docs page titled only "Overview" is the *agent* overview, and its path
    is the only place that says so. Prepending "Overview" to every chunk
    would add nothing; "Agent Overview" is what makes them findable.
    """

    from app.services.web_crawler import _extract_title

    html = "<html><head><title>Overview | Acme Docs</title></head><body>x</body></html>"
    label = _extract_title(html, url="https://acme.com/docs/agent/overview")

    assert label == "Agent Overview"


def test_a_root_page_falls_back_to_its_title() -> None:
    """A path with no segments describes nothing, so the title answers."""

    from app.services.web_crawler import _extract_title

    html = "<html><head><title>Acme - build faster</title></head><body>x</body></html>"

    assert _extract_title(html, url="https://acme.com/") == "Acme - build faster"


def test_an_opaque_path_falls_back_to_its_title() -> None:
    """
    Not every site writes readable URLs. A hash or an id describes nothing,
    and a label of "P 7f3a9b" would be worse than the title it replaced.
    """

    from app.services.web_crawler import _extract_title

    html = "<html><head><title>Spring sale</title></head><body>x</body></html>"

    assert _extract_title(html, url="https://acme.com/p/7f3xzb") == "Spring sale"


def test_a_page_with_no_title_uses_its_heading() -> None:
    from app.services.web_crawler import _extract_title

    html = "<html><body><h1>Refund policy</h1></body></html>"

    assert _extract_title(html, url="https://acme.com/") == "Refund policy"
