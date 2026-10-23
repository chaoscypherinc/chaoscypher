# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The pieces ``discover_site`` is built from: URL identity, scope, parsers, throttle."""

from __future__ import annotations

import gzip

import pytest

from chaoscypher_core.services.sources.web.site_parsers import (
    parse_html_links,
    parse_llms_txt,
    parse_sitemap,
)
from chaoscypher_core.services.sources.web.site_urls import (
    SiteScope,
    clean_url,
    page_identity,
    url_key,
)

from .conftest import FakeClock


# --------------------------------------------------------------------------- URLs


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("HTTPS://Docs.Example.COM:443/a/b#frag", "https://docs.example.com/a/b"),
        ("http://docs.example.com:80/a?q=1#x", "http://docs.example.com/a?q=1"),
        ("https://docs.example.com:8443/a/", "https://docs.example.com:8443/a/"),
        ("https://docs.example.com", "https://docs.example.com/"),
    ],
)
def test_clean_url(raw: str, cleaned: str) -> None:
    assert clean_url(raw) == cleaned


def test_url_key_folds_the_trailing_slash_and_keeps_the_query() -> None:
    assert url_key("https://d.example.com/a/") == url_key("https://d.example.com/a")
    assert url_key("https://d.example.com/a#x") == url_key("https://d.example.com/a")
    assert url_key("https://d.example.com/a?v=1") != url_key("https://d.example.com/a")
    assert url_key("https://d.example.com/") == url_key("https://d.example.com")


@pytest.mark.parametrize(
    ("start", "inside", "outside"),
    [
        (
            "https://d.example.com/docs",
            ["https://d.example.com/docs", "https://d.example.com/docs/a/b?x=1"],
            [
                "https://d.example.com/docsearch",
                "https://d.example.com/blog",
                "http://d.example.com/docs/a",
                "https://e.example.com/docs/a",
                "https://d.example.com:8443/docs/a",
            ],
        ),
        (
            "https://d.example.com/docs/index.html",
            ["https://d.example.com/docs/other"],
            ["https://d.example.com/"],
        ),
        ("https://d.example.com/", ["https://d.example.com/anything"], ["https://x.example.com/"]),
    ],
)
def test_scope(start: str, inside: list[str], outside: list[str]) -> None:
    scope = SiteScope.from_url(start)
    for url in inside:
        assert scope.contains(url), url
    for url in outside:
        assert not scope.contains(url), url


@pytest.mark.parametrize(
    ("start", "prefix"),
    [
        # A version-like segment names a directory, not a file: the scope stays on it.
        ("https://docs.python.org/3.12", "/3.12"),
        ("https://docs.djangoproject.com/en/5.0", "/en/5.0"),
        # Known page suffixes name their directory.
        ("https://d.example.com/docs/index.html", "/docs"),
        ("https://d.example.com/docs/guide.md", "/docs"),
        ("https://d.example.com/docs/page.aspx", "/docs"),
    ],
)
def test_scope_drops_the_last_segment_only_for_a_known_file_suffix(start: str, prefix: str) -> None:
    assert SiteScope.from_url(start).path_prefix == prefix


def test_page_identity_folds_the_markdown_twin() -> None:
    """``/a.md`` (an llms.txt link) and ``/a`` (a sitemap or a crawl) are one page."""
    assert page_identity("https://d.example.com/a.md") == page_identity("https://d.example.com/a")
    assert page_identity("https://d.example.com/a/index.md") == page_identity(
        "https://d.example.com/a/"
    )
    assert page_identity("https://d.example.com/a.md") != page_identity("https://d.example.com/b")


def test_the_root_index_twin_folds_to_the_root() -> None:
    """``/index.md`` at the root is the root page, spelled the way ``/`` is."""
    root = page_identity("https://d.example.com/")
    assert root == "https://d.example.com/"
    assert page_identity("https://d.example.com/index.md") == root
    assert page_identity("https://d.example.com/index.html.md") == root
    assert page_identity("https://d.example.com") == root
    # The query stays, and the twin folds before it.
    assert page_identity("https://d.example.com/a.md?v=2") == "https://d.example.com/a?v=2"


def test_scope_documents() -> None:
    scope = SiteScope.from_url("https://d.example.com/docs/guide/")
    assert scope.origin == "https://d.example.com"
    assert scope.document_url("llms.txt") == "https://d.example.com/docs/guide/llms.txt"
    assert SiteScope.from_url("https://d.example.com").document_url("llms.txt") == (
        "https://d.example.com/llms.txt"
    )


# --------------------------------------------------------------------------- parsers


def test_parse_llms_txt_title_links_and_md_preference() -> None:
    text = (
        "# Product Docs\n\n> Summary with a [home link](https://d.example.com/).\n\n"
        "## Docs\n\n- [Intro](intro): the start\n- [Intro md](intro.md)\n"
        '- [Guide](https://d.example.com/docs/guide.md "Guide title")\n'
        "- [Full](llms-full.txt)\n\n## Optional\n\n- [Changelog](/docs/changelog.md)\n"
    )

    title, links = parse_llms_txt(text, "https://d.example.com/docs/llms.txt")

    assert title == "Product Docs"
    assert links == [
        ("https://d.example.com/", "home link"),
        ("https://d.example.com/docs/intro.md", "Intro md"),
        ("https://d.example.com/docs/guide.md", "Guide"),
        ("https://d.example.com/docs/changelog.md", "Changelog"),
    ]


def test_parse_sitemap_urlset_index_and_gzip() -> None:
    urlset = (
        b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc> https://d.example.com/a </loc></url><url><loc>https://d.example.com/b</loc>"
        b"</url></urlset>"
    )
    index = (
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://d.example.com/s1.xml</loc></sitemap></sitemapindex>"
    )

    assert parse_sitemap(urlset, max_bytes=10_000) == (
        ["https://d.example.com/a", "https://d.example.com/b"],
        [],
    )
    assert parse_sitemap(index, max_bytes=10_000) == ([], ["https://d.example.com/s1.xml"])
    assert parse_sitemap(gzip.compress(urlset), max_bytes=10_000)[0] == [
        "https://d.example.com/a",
        "https://d.example.com/b",
    ]


def test_parse_sitemap_rejects_junk_and_bombs() -> None:
    assert parse_sitemap(b"<html><body>not a sitemap</body></html>", max_bytes=1000) == ([], [])
    assert parse_sitemap(b"\x00 not xml", max_bytes=1000) == ([], [])
    # A gzip body that inflates past the cap is refused, not inflated.
    assert parse_sitemap(gzip.compress(b" " * 50_000), max_bytes=1000) == ([], [])


def test_parse_html_links_title_and_absolute_hrefs() -> None:
    html = (
        b"<html><head><title> My  Page </title></head><body>"
        b'<a href="a">A</a><a href="/b#x">B</a><a href="https://e.example.com/c">C</a>'
        b'<a name="anchor-only">no href</a></body></html>'
    )

    title, links = parse_html_links(html, "https://d.example.com/docs/")

    assert title == "My Page"
    assert links == [
        "https://d.example.com/docs/a",
        "https://d.example.com/b#x",
        "https://e.example.com/c",
    ]


# --------------------------------------------------------------------------- throttle


@pytest.mark.asyncio
async def test_throttle_spaces_request_starts_and_honours_a_raised_delay() -> None:
    clock = FakeClock()
    throttle = clock.throttle(delay_seconds=1.0, concurrency=1)

    for _ in range(3):
        async with throttle.slot():
            pass
    throttle.raise_delay(4.0)
    throttle.raise_delay(2.0)  # smaller: ignored
    async with throttle.slot():
        pass

    assert clock.slept == [1.0, 1.0, 4.0]
    assert throttle.delay_seconds == 4.0


@pytest.mark.asyncio
async def test_throttle_turns_away_a_start_past_the_deadline_without_waiting() -> None:
    """A request that could start only after the deadline is refused at once and not counted."""
    clock = FakeClock()
    throttle = clock.throttle(delay_seconds=1.0, concurrency=1)

    async with throttle.slot(deadline=5.0) as first:
        pass
    async with throttle.slot(deadline=0.5) as late:  # would start at 1.0
        pass
    async with throttle.slot(deadline=1.0) as on_time:  # starts exactly at 1.0
        pass

    assert (first, late, on_time) == (True, False, True)
    assert clock.slept == [1.0]  # the refused start slept nothing
    assert throttle.now() == 1.0
