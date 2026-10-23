# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A site import run in this process, without the queue (the CLI and the MCP server).

The same ``import_site`` operation the worker runs: discovery, every page
fetched and staged through the URL import path, a changed page re-imported in
place. With no indexing queue the pages are left ``pending`` for the caller's
own pipeline (``pending_site_pages``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.models import SiteStatus, SourceStatus
from chaoscypher_core.operations.sources.site_in_process import (
    find_site,
    pending_site_pages,
    run_site_import,
)
from chaoscypher_core.operations.sources.site_runs import create_site_row
from chaoscypher_core.services.sources.web import DiscoveryMethod, SiteManifest, SitePage
from chaoscypher_core.services.sources.web.discovery import pages_found

from .conftest import FakeClock
from .site_harness import DB, ROOT, SiteHarness, build_harness, close_harness, markdown_page


if TYPE_CHECKING:
    from .conftest import FakeSite


_MD = "text/markdown; charset=utf-8"


@pytest.fixture
def harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_site: FakeSite
) -> Iterator[SiteHarness]:
    built = build_harness(tmp_path, monkeypatch)
    yield built
    close_harness(built, tmp_path)


def _serve(site: FakeSite, pages: dict[str, str]) -> None:
    site.routes.clear()
    site.page(ROOT, "Example Docs", [])
    links = "".join(f"- [{name}]({ROOT}/{name}.md)\n" for name in pages)
    site.add(f"{ROOT}/llms.txt", f"# Example Docs\n\n{links}", content_type=_MD)
    for name, body in pages.items():
        site.add(f"{ROOT}/{name}.md", body, content_type=_MD)


async def _run(harness: SiteHarness, site_id: str, *, refresh: bool = False) -> dict:
    return await run_site_import(
        harness.adapter,
        site_id=site_id,
        database_name=DB,
        refresh=refresh,
        settings=harness.settings,
    )


def test_create_site_row_queues_a_site_named_by_its_address(harness: SiteHarness) -> None:
    site = create_site_row(
        harness.adapter, url=ROOT, database_name=DB, options={"auto_confirm": True}
    )

    stored = harness.adapter.get_site(site["id"], DB)
    assert stored is not None
    assert stored["status"] == SiteStatus.QUEUED
    assert stored["title"] == ROOT
    # Stored under the site rules: no page waits for confirmation or is skipped as a duplicate.
    assert stored["options"] == {"auto_confirm": True, "skip_duplicates": False}


@pytest.mark.asyncio
async def test_an_in_process_import_leaves_every_page_pending(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"a": markdown_page("A"), "b": markdown_page("B")})
    site = create_site_row(harness.adapter, url=ROOT, database_name=DB, options={})

    result = await _run(harness, site["id"])

    assert result["status"] == "ok" and result["added"] == 2
    pending = pending_site_pages(harness.adapter, site["id"], DB)
    assert sorted(p["origin_url"] for p in pending) == [f"{ROOT}/a.md", f"{ROOT}/b.md"]
    stored = harness.adapter.get_site(site["id"], DB)
    assert stored is not None and stored["status"] == SiteStatus.IMPORTED
    assert stored["title"] == "Example Docs"
    # No indexing queue exists in this process: nothing was queued.
    assert harness.queue.calls == []


@pytest.mark.asyncio
async def test_an_in_process_refresh_leaves_only_the_changed_page_pending(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"same": markdown_page("Same"), "edited": markdown_page("Edited")})
    site = create_site_row(harness.adapter, url=ROOT, database_name=DB, options={})
    await _run(harness, site["id"])
    for page in pending_site_pages(harness.adapter, site["id"], DB):
        harness.adapter.update_file(page["id"], DB, {"status": SourceStatus.COMMITTED})

    _serve(
        fake_site,
        {"same": markdown_page("Same"), "edited": markdown_page("Edited, with more words")},
    )
    result = await _run(harness, site["id"], refresh=True)

    assert result["updated"] == 1 and result["unchanged"] == 1
    assert [p["origin_url"] for p in pending_site_pages(harness.adapter, site["id"], DB)] == [
        f"{ROOT}/edited.md"
    ]
    assert harness.queue.calls == []


@pytest.mark.asyncio
async def test_an_in_process_run_past_its_time_continues_in_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_site: FakeSite
) -> None:
    """With no queue the continuation is a loop: every page, one summed result, nothing queued."""
    from chaoscypher_core.operations import queue_utils

    built = build_harness(tmp_path, monkeypatch, site_run_max_seconds=2.5)
    try:
        _serve(fake_site, {name: markdown_page(name) for name in ("a", "b", "c", "d")})
        site = create_site_row(built.adapter, url=ROOT, database_name=DB, options={})

        async def _no_queue(*_args: object, **_kwargs: object) -> str:
            msg = "an in-process run never queues"
            raise AssertionError(msg)

        monkeypatch.setattr(queue_utils, "queue_import_site", _no_queue)
        clock = FakeClock()

        result = await run_site_import(
            built.adapter,
            site_id=site["id"],
            database_name=DB,
            settings=built.settings,
            throttle=clock.throttle(delay_seconds=1.0, concurrency=1),
        )

        assert result["status"] == "ok" and result["added"] == 4
        assert len(pending_site_pages(built.adapter, site["id"], DB)) == 4
        stored = built.adapter.get_site(site["id"], DB)
        assert stored is not None and stored["status"] == SiteStatus.IMPORTED
    finally:
        close_harness(built, tmp_path)


def test_find_site_by_id_address_or_title(harness: SiteHarness) -> None:
    docs = create_site_row(harness.adapter, url=f"{ROOT}/docs", database_name=DB, options={})
    other = create_site_row(
        harness.adapter, url="https://example.com", database_name=DB, options={}
    )
    harness.adapter.update_site(docs["id"], DB, {"title": "Example Docs"})

    def found(ref: str) -> str | None:
        site = find_site(harness.adapter, DB, ref, page_size=1)
        return site["id"] if site else None

    assert found(docs["id"]) == docs["id"]
    assert found(f"{ROOT}/docs/") == docs["id"]  # the trailing slash folded
    assert found("example docs") == docs["id"]  # a title, any case
    assert found("https://example.com") == other["id"]  # past the first page of one
    assert found("Nothing like it") is None


def _manifest(method: DiscoveryMethod, count: int, *, capped: bool = False) -> SiteManifest:
    return SiteManifest(
        url=ROOT,
        title="Example Docs",
        method=method,
        pages=[SitePage(url=f"{ROOT}/p{i}") for i in range(count)],
        crawl_delay_seconds=0.0,
        capped=capped,
    )


@pytest.mark.parametrize(
    ("manifest", "phrase"),
    [
        (_manifest(DiscoveryMethod.LLMS_TXT, 71), "71 pages found by llms.txt"),
        (_manifest(DiscoveryMethod.SITEMAP, 1), "1 page found by sitemap"),
        (_manifest(DiscoveryMethod.CRAWL, 25, capped=True), "at least 25 pages found by crawling"),
        (_manifest(DiscoveryMethod.NONE, 0), "no pages found"),
    ],
)
def test_pages_found_says_how_many_and_how(manifest: SiteManifest, phrase: str) -> None:
    assert pages_found(manifest) == phrase
