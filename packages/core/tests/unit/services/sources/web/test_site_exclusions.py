# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A site remembers the pages the user left out; a refresh keeps them out (§ 8 item 18).

The pages unticked at import, or passed as ``exclude`` to a refresh, are
stored on the site (``source_sites.excluded_pages``, migration 0023) as page
identities (``page_identity``), so another spelling of the same address still
matches. A refresh that passes no list keeps the stored one; one that passes a
list replaces it (a page comes back that way). A page the user deletes from
the site joins the list. A page the site gains later is still added. Real
database, real WebScraper, fake web site.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.sources.site_runs import (
    begin_site_refresh,
    create_site_row,
    exclusion_keys,
    name_pages,
)
from chaoscypher_core.services.graph.management.source import SourceService

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


def _serve(site: FakeSite, names: list[str]) -> None:
    site.routes.clear()
    site.page(ROOT, "Example Docs", [])
    links = "".join(f"- [{name}]({ROOT}/{name}.md)\n" for name in names)
    site.add(f"{ROOT}/llms.txt", f"# Example Docs\n\n{links}", content_type=_MD)
    for name in names:
        site.add(f"{ROOT}/{name}.md", markdown_page(name.title()), content_type=_MD)


def _url(name: str) -> str:
    return f"{ROOT}/{name}.md"


def _settle(harness: SiteHarness) -> None:
    """The pipeline ran: every page is committed (a refresh then compares hashes)."""
    for source in harness.pages().values():
        harness.adapter.update_file(source["id"], DB, {"status": SourceStatus.COMMITTED})


@pytest.mark.asyncio
async def test_pages_left_out_at_import_stay_out_on_a_refresh(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, ["intro", "install", "faq"])
    site_id = harness.create_site(exclude=[_url("faq")])
    await harness.run(site_id)
    _settle(harness)

    result = await harness.run(site_id, refresh=True)

    assert sorted(harness.pages(site_id)) == [_url("install"), _url("intro")]
    assert result["added"] == 0
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None
    # Stored as the page's identity (the .md twin folded), the way runs match pages.
    assert site["excluded_pages"] == [f"{ROOT}/faq"]


@pytest.mark.asyncio
async def test_another_spelling_of_a_left_out_page_stays_out(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, ["intro", "faq", "deploy"])
    # Upper-case host, trailing slash, a fragment, and the .md twin: one page each.
    site_id = harness.create_site(exclude=["HTTPS://Docs.Example.com/faq/", f"{ROOT}/deploy#top"])
    await harness.run(site_id)
    _settle(harness)
    await harness.run(site_id, refresh=True)

    assert sorted(harness.pages(site_id)) == [_url("intro")]


@pytest.mark.asyncio
async def test_a_refresh_with_a_new_list_replaces_the_stored_one_and_brings_a_page_back(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, ["intro", "install", "faq"])
    site_id = harness.create_site(exclude=[_url("faq"), _url("install")])
    await harness.run(site_id)
    _settle(harness)

    result = await harness.run(site_id, refresh=True, exclude=[_url("install")])

    assert sorted(harness.pages(site_id)) == [_url("faq"), _url("intro")]
    assert result["added"] == 1
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["excluded_pages"] == [f"{ROOT}/install"]

    # An empty list brings every page back.
    _settle(harness)
    await harness.run(site_id, refresh=True, exclude=[])
    assert sorted(harness.pages(site_id)) == [_url("faq"), _url("install"), _url("intro")]


@pytest.mark.asyncio
async def test_a_page_the_site_gains_is_still_added_beside_the_left_out_ones(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, ["intro", "faq"])
    site_id = harness.create_site(exclude=[_url("faq")])
    await harness.run(site_id)
    _settle(harness)
    _serve(fake_site, ["intro", "faq", "added"])

    result = await harness.run(site_id, refresh=True)

    assert sorted(harness.pages(site_id)) == [_url("added"), _url("intro")]
    assert result["added"] == 1


@pytest.mark.asyncio
async def test_a_page_deleted_from_its_site_stays_out_on_a_refresh(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, ["intro", "faq"])
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)
    faq = harness.pages(site_id)[_url("faq")]

    # The user's delete: the one the web UI, the CLI and the MCP server run.
    assert SourceService(harness.adapter, DB).delete_source(faq["id"])
    await harness.run(site_id, refresh=True)

    assert sorted(harness.pages(site_id)) == [_url("intro")]
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["excluded_pages"] == [f"{ROOT}/faq"]


def test_create_site_row_stores_the_left_out_pages_normalized(harness: SiteHarness) -> None:
    site = create_site_row(
        harness.adapter,
        url=ROOT,
        database_name=DB,
        options={},
        exclude=[f"{ROOT}/b.md", f"{ROOT}/a/", f"{ROOT}/b"],
    )

    stored = harness.adapter.get_site(site["id"], DB)
    assert stored is not None
    assert stored["excluded_pages"] == [f"{ROOT}/a", f"{ROOT}/b"]
    plain = create_site_row(harness.adapter, url=f"{ROOT}/x", database_name=DB, options={})
    assert plain["excluded_pages"] == []


@pytest.mark.parametrize(
    "bad",
    [
        "docs.example.com/faq",  # no scheme
        "/faq",  # a bare path
        "not a url",
        "https://other.example.com/faq",  # another host
        "https://docs.example.com/blog/x",  # outside the site's /docs scope
    ],
)
def test_an_address_that_names_no_page_of_the_site_is_refused(
    harness: SiteHarness, bad: str
) -> None:
    """A bad entry never becomes an empty list (which would bring every page back)."""
    docs = f"{ROOT}/docs"
    with pytest.raises(ValidationError) as caught:
        create_site_row(
            harness.adapter,
            url=docs,
            database_name=DB,
            options={},
            exclude=[f"{docs}/ok", bad],
        )
    assert caught.value.field == "exclude"
    assert bad in caught.value.message
    assert f"{docs}/ok" not in caught.value.message

    site_id = harness.create_site(docs, exclude=[f"{docs}/kept"])
    with pytest.raises(ValidationError):
        begin_site_refresh(harness.adapter, site_id, DB, exclude=[bad])
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["excluded_pages"] == [f"{docs}/kept"]


@pytest.mark.asyncio
async def test_an_http_spelling_of_a_left_out_page_is_stored_in_the_sites_scheme(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, ["intro", "faq"])
    site_id = harness.create_site(exclude=["http://docs.example.com/faq.md"])

    await harness.run(site_id)

    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["excluded_pages"] == [f"{ROOT}/faq"]
    assert sorted(harness.pages(site_id)) == [_url("intro")]


def test_a_shorter_list_names_the_pages_that_come_back(harness: SiteHarness) -> None:
    site_id = harness.create_site(exclude=[_url("a"), _url("b"), _url("c")])

    assert begin_site_refresh(harness.adapter, site_id, DB, exclude=[_url("b")]) == [
        f"{ROOT}/a",
        f"{ROOT}/c",
    ]
    assert begin_site_refresh(harness.adapter, site_id, DB) == []  # the list stays
    assert begin_site_refresh(harness.adapter, site_id, DB, exclude=[]) == [f"{ROOT}/b"]


@pytest.mark.asyncio
async def test_a_page_deleted_during_a_run_is_not_added_again(
    harness: SiteHarness, fake_site: FakeSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run re-reads the site's list before each page, not once at its start."""
    _serve(fake_site, ["intro", "faq"])
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)
    faq = harness.pages(site_id)[_url("faq")]
    # Its fetch failed last time: a refresh deletes the placeholder and adds it again.
    harness.adapter.update_file(
        faq["id"], DB, {"status": SourceStatus.ERROR, "error_stage": "url_fetch"}
    )

    from chaoscypher_core.operations.sources import site_import_handler

    real_fetch = site_import_handler.fetch_page
    deleted: list[str] = []

    async def _fetch_then_delete(url: str, settings: object) -> object:
        if not deleted:  # the user deletes the page while the run is under way
            deleted.append(faq["id"])
            assert SourceService(harness.adapter, DB).delete_source(faq["id"])
        return await real_fetch(url, settings)

    monkeypatch.setattr(site_import_handler, "fetch_page", _fetch_then_delete)
    await harness.run(site_id, refresh=True)

    assert sorted(harness.pages(site_id)) == [_url("intro")]


def test_a_page_deleted_with_its_site_is_not_written_to_the_list(harness: SiteHarness) -> None:
    """Deleting a site with its pages does not write the list of a row about to go."""
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

    site_id = harness.create_site()
    harness.adapter.upload_source(
        source_id="p1",
        database_name=DB,
        filename="p1.md",
        file_content=b"# page\n",
        staging_dir=str(Path(harness.adapter.db_path).parent / "sources"),
        origin_url=_url("p1"),
        source_type_override="webpage",
        site_id=site_id,
    )
    writes: list[dict[str, object]] = []
    real_update = SqliteAdapter.update_site

    def _record(self: SqliteAdapter, sid: str, db: str, updates: dict[str, object]) -> None:
        writes.append(dict(updates))
        real_update(self, sid, db, updates)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SqliteAdapter, "update_site", _record)
        assert SourceService(harness.adapter, DB).delete_source("p1", leave_out_of_site=False)

    assert writes == []


def test_pages_are_named_a_few_at_a_time() -> None:
    urls = [f"{ROOT}/p{n}" for n in range(8)]
    assert name_pages(urls[:2], limit=5) == f"{ROOT}/p0, {ROOT}/p1"
    assert name_pages(urls, limit=5) == ", ".join(urls[:5]) + " and 3 more"
    assert name_pages([], limit=5) == ""


def test_exclusion_keys_keep_none_and_cap_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    settings = SimpleNamespace(web=SimpleNamespace(site_max_pages=2))
    monkeypatch.setattr("chaoscypher_core.app_config.get_settings", lambda: settings)

    assert exclusion_keys(None, site_url=ROOT) is None
    assert exclusion_keys([], site_url=ROOT) == []
    with pytest.raises(ValidationError) as caught:
        exclusion_keys([_url("a"), _url("b"), _url("c")], site_url=ROOT)
    assert caught.value.field == "exclude"
