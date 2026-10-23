# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``import_site``: a site row's pages discovered, fetched under the throttle and uploaded.

Each page goes through the single-URL import path (``import_url_page``): a
placeholder, the shared ``fetch_page``, the upload pipeline, with the site's
id, the page's address and content hash, ``source_type="webpage"`` and the
site's stored upload options. A page that fails is recorded on its own row
and does not stop the site. Real database, real WebScraper, fake web site.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.services.sources.web import progress_by_site

from .conftest import PRIVATE_HOST, FakeClock, FakeSite
from .site_harness import (
    DB,
    ROOT,
    SiteHarness,
    build_harness,
    close_harness,
    markdown_page,
)


_MD = "text/markdown; charset=utf-8"
PAGES = ["intro", "install", "configure", "deploy", "faq"]


@pytest.fixture
def harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_site: FakeSite
) -> Iterator[SiteHarness]:
    built = build_harness(tmp_path, monkeypatch)
    yield built
    close_harness(built, tmp_path)


def _serve_five_pages(site: FakeSite, *, robots: str | None = None) -> None:
    site.page(ROOT, "Example Docs", [])
    links = "".join(f"- [{name.title()}]({ROOT}/{name}.md)\n" for name in PAGES)
    site.add(f"{ROOT}/llms.txt", f"# Example Docs\n\n{links}", content_type=_MD)
    for name in PAGES:
        site.add(f"{ROOT}/{name}.md", markdown_page(name.title()), content_type=_MD)
    if robots is not None:
        site.add(f"{ROOT}/robots.txt", robots, content_type="text/plain")


@pytest.mark.asyncio
async def test_five_pages_import_as_five_sources_of_one_site(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve_five_pages(fake_site)
    site_id = harness.create_site(options={"auto_confirm": True, "extraction_depth": "quick"})

    result = await harness.run(site_id)

    pages = harness.pages(site_id)
    assert sorted(pages) == sorted(f"{ROOT}/{name}.md" for name in PAGES)
    for url, source in pages.items():
        assert source["site_id"] == site_id
        assert source["source_type"] == "webpage"
        assert source["status"] == "pending"
        assert source["extraction_depth"] == "quick"  # the site's stored options
        name = url.rsplit("/", 1)[1].removesuffix(".md")
        expected = hashlib.sha256(markdown_page(name.title()).encode()).hexdigest()
        assert source["content_hash"] == expected
    assert len(harness.queue.calls) == 5
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None
    assert site["status"] == "imported"
    assert site["page_count"] == 5
    assert site["discovery_method"] == "llms_txt"
    assert site["title"] == "Example Docs"
    assert site["last_checked_at"] is not None
    assert result["added"] == 5 and result["failed"] == 0


@pytest.mark.asyncio
async def test_a_page_that_fails_leaves_the_others_and_shows_in_progress(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve_five_pages(fake_site)
    # One page now answers 500, another redirects to a private address (SSRF).
    fake_site.add(f"{ROOT}/deploy.md", "boom", content_type="text/plain", status=500)
    fake_site.redirect(f"{ROOT}/faq.md", f"https://{PRIVATE_HOST}/secrets")
    site_id = harness.create_site()

    result = await harness.run(site_id)

    pages = harness.pages(site_id)
    assert len(pages) == 5
    failed = {url for url, row in pages.items() if row["status"] == "error"}
    assert failed == {f"{ROOT}/deploy.md", f"{ROOT}/faq.md"}
    assert all(pages[url]["error_stage"] == "url_fetch" for url in failed)
    assert not any(PRIVATE_HOST in url for url in fake_site.requested)
    assert result["added"] == 3 and result["failed"] == 2
    progress = progress_by_site(harness.adapter.count_site_pages([site_id], DB))[site_id]
    assert progress.total == 5 and progress.failed == 2 and progress.in_progress == 3
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["status"] == "imported"


@pytest.mark.asyncio
async def test_a_private_site_address_fails_the_site(harness: SiteHarness) -> None:
    site_id = harness.create_site(f"https://{PRIVATE_HOST}/docs")

    result = await harness.run(site_id)

    site = harness.adapter.get_site(site_id, DB)
    assert site is not None
    assert site["status"] == "failed"
    assert "blocked" in (site["error_message"] or "")
    assert result["status"] == "error"
    assert harness.pages(site_id) == {}


@pytest.mark.asyncio
async def test_excluded_pages_are_left_out_and_the_rest_imported(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    """The dialog sends what was unticked, so a capped preview never drops the rest."""
    _serve_five_pages(fake_site)
    site_id = harness.create_site(exclude=[f"{ROOT}/intro.md", f"{ROOT}/faq", f"{ROOT}/nope.md"])

    await harness.run(site_id)

    assert sorted(harness.pages(site_id)) == [
        f"{ROOT}/configure.md",
        f"{ROOT}/deploy.md",
        f"{ROOT}/install.md",
    ]


@pytest.mark.asyncio
async def test_a_page_redirected_off_the_site_or_onto_a_disallowed_path_is_refused(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    """A page's final address after redirects passes the same scope and robots.txt as discovery."""
    _serve_five_pages(fake_site, robots="User-agent: *\nDisallow: /private\n")
    fake_site.redirect(f"{ROOT}/deploy.md", "https://other.example.org/deploy.md")
    fake_site.add(
        "https://other.example.org/deploy.md", markdown_page("Elsewhere"), content_type=_MD
    )
    fake_site.redirect(f"{ROOT}/faq.md", f"{ROOT}/private/faq.md")
    fake_site.add(f"{ROOT}/private/faq.md", markdown_page("Private"), content_type=_MD)
    site_id = harness.create_site()

    result = await harness.run(site_id)

    pages = harness.pages(site_id)
    refused = {url for url, row in pages.items() if row["status"] == "error"}
    assert refused == {f"{ROOT}/deploy.md", f"{ROOT}/faq.md"}
    assert all("outside the site" in (pages[url]["error_message"] or "") for url in refused)
    assert result["added"] == 3 and result["failed"] == 2


@pytest.mark.asyncio
async def test_skip_duplicates_never_takes_a_page_from_its_site(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    """A page whose content is already a source is still imported as the site's own page.

    Attaching the existing source instead would hand the user's own source to
    the site (a "delete the site and its pages" would then delete it), so the
    site import ignores skip_duplicates for its pages.
    """
    _serve_five_pages(fake_site)
    existing = await harness.service.upload_file(
        file_content=markdown_page("Intro").encode(), filename="intro.md"
    )
    site_id = harness.create_site(options={"skip_duplicates": True, "auto_confirm": True})

    result = await harness.run(site_id)

    pages = harness.pages(site_id)
    assert len(pages) == 5 and pages[f"{ROOT}/intro.md"]["site_id"] == site_id
    assert pages[f"{ROOT}/intro.md"]["id"] != existing["id"]
    mine = harness.adapter.get_source(existing["id"], DB)
    assert mine is not None and mine["site_id"] is None
    assert result["added"] == 5


@pytest.mark.asyncio
async def test_no_site_page_waits_for_domain_confirmation_whatever_the_options(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve_five_pages(fake_site)
    site_id = harness.create_site(options={"auto_confirm": False})

    await harness.run(site_id)

    pages = harness.pages(site_id)
    assert len(pages) == 5
    assert not any(row["confirmation_required"] for row in pages.values())


@pytest.mark.asyncio
async def test_the_page_count_is_known_as_soon_as_discovery_ends(
    harness: SiteHarness, fake_site: FakeSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Progress reads "1 of 5" mid-import, never "1 of 1"."""
    from chaoscypher_core.operations.sources import site_import_handler

    _serve_five_pages(fake_site)
    site_id = harness.create_site()
    seen: list[int] = []
    real_import = site_import_handler.import_url_page

    async def _watching_import(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        site = harness.adapter.get_site(site_id, DB)
        assert site is not None
        seen.append(site["page_count"])
        return await real_import(url, *args, **kwargs)

    monkeypatch.setattr(site_import_handler, "import_url_page", _watching_import)

    await harness.run(site_id)

    assert seen == [5] * 5


@pytest.mark.asyncio
async def test_an_unexpected_error_on_one_page_is_that_pages_failure(
    harness: SiteHarness, fake_site: FakeSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.operations.sources import site_import_handler

    _serve_five_pages(fake_site)
    site_id = harness.create_site()
    real_import = site_import_handler.import_url_page

    async def _one_breaks(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if url.endswith("/install.md"):
            msg = "the page went away mid-run"
            raise RuntimeError(msg)
        return await real_import(url, *args, **kwargs)

    monkeypatch.setattr(site_import_handler, "import_url_page", _one_breaks)

    result = await harness.run(site_id)

    assert result["status"] == "ok"
    assert result["added"] == 4 and result["failed"] == 1
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["status"] == "imported"


# --------------------------------------------------------------------------- resumable runs


@pytest.mark.asyncio
async def test_a_run_cut_off_part_way_continues_from_its_cursor(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    """A run killed at the third page (the operations timeout's cancel) resumes there."""
    _serve_five_pages(fake_site)
    site_id = harness.create_site()

    def _timeout_at_configure(request: Any) -> None:
        if request.url.path == "/configure.md":
            raise asyncio.CancelledError

    fake_site.on_request = _timeout_at_configure
    with pytest.raises(asyncio.CancelledError):
        await harness.run(site_id)
    cut = harness.adapter.get_site(site_id, DB)
    assert cut is not None and cut["status"] == "failed" and cut["run_cursor"] == 2

    fake_site.on_request = None
    fake_site.requested.clear()
    result = await harness.run(site_id, refresh=True)

    page_fetches = [u for u in fake_site.requested if u.endswith(".md")]
    assert page_fetches == [f"{ROOT}/{name}.md" for name in ("configure", "deploy", "faq")]
    assert sorted(harness.pages(site_id)) == sorted(f"{ROOT}/{name}.md" for name in PAGES)
    assert result["status"] == "ok"
    done = harness.adapter.get_site(site_id, DB)
    assert done is not None and done["status"] == "imported" and done["run_cursor"] is None


@pytest.mark.asyncio
async def test_a_run_past_its_time_hands_the_rest_to_a_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_site: FakeSite
) -> None:
    """A run stops before ``web.site_run_max_seconds``, saves its cursor and queues the rest."""
    from chaoscypher_core.operations import queue_utils

    built = build_harness(tmp_path, monkeypatch, site_run_max_seconds=2.5)
    try:
        _serve_five_pages(fake_site)
        site_id = built.create_site()
        queued: list[dict[str, Any]] = []

        async def _record(site_id: str, **kwargs: Any) -> str:
            queued.append({"site_id": site_id, **kwargs})
            return "task-2"

        monkeypatch.setattr(queue_utils, "queue_import_site", _record)
        clock = FakeClock()

        first = await built.run(site_id, throttle=clock.throttle(delay_seconds=1.0, concurrency=1))

        assert first["status"] == "continued"
        site = built.adapter.get_site(site_id, DB)
        assert site is not None and site["status"] == "queued" and site["run_cursor"] == 1
        assert [q["site_id"] for q in queued] == [site_id]
        assert queued[0]["refresh"] is False

        fake_site.requested.clear()
        second = await built.run(site_id)

        page_fetches = [u for u in fake_site.requested if u.endswith(".md")]
        assert f"{ROOT}/intro.md" not in page_fetches and len(page_fetches) == 4
        assert second["status"] == "ok"
        assert len(built.pages(site_id)) == 5
    finally:
        close_harness(built, tmp_path)


@pytest.mark.asyncio
async def test_every_request_keeps_the_crawl_delay(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    """Discovery and page fetches share one throttle; robots.txt's larger delay holds for all."""
    _serve_five_pages(fake_site, robots="User-agent: *\nCrawl-delay: 3\n")
    site_id = harness.create_site()
    clock = FakeClock()

    await harness.run(site_id, throttle=clock.throttle(delay_seconds=1.0))

    page_fetches = [u for u in fake_site.requested if u.endswith(".md")]
    assert len(page_fetches) == 5
    # Every start after the first waited; once robots.txt was read, the gap is 3 s.
    assert len(clock.slept) == len(fake_site.requested) - 1
    assert clock.slept[-5:] == [3.0] * 5
    assert all(gap in (1.0, 3.0) for gap in clock.slept)


@pytest.mark.asyncio
async def test_a_deleted_site_is_a_clean_no_op(harness: SiteHarness) -> None:
    result = await harness.run("missing")
    assert result["status"] == "error"
