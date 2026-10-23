# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A site refresh: discovery again, every page fetched, only what changed is touched (decision 7).

Between two runs the fake site changes: one page stays the same, one is
edited, one is added, one is removed. The unchanged page is left alone; the
edited one is re-imported in place (same source id: its graph rows released
through ``force_re_extract``, its new content staged and indexing queued);
the new one is added with the site's id; the removed one is marked gone on
the site, never deleted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import NodeCreate, SourceStatus, TemplateCreate

from .site_harness import (
    BODY,
    DB,
    ROOT,
    SiteHarness,
    build_harness,
    close_harness,
    markdown_page,
)


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


def _settle(harness: SiteHarness) -> None:
    """The pipeline ran: every page is committed."""
    for source in harness.pages().values():
        harness.adapter.update_file(
            source["id"],
            DB,
            {"status": SourceStatus.COMMITTED, "indexing_complete": True, "commit_complete": True},
        )


def _staged_text(filepath: str) -> str:
    return Path(filepath).read_text(encoding="utf-8")


def _url(name: str) -> str:
    return f"{ROOT}/{name}.md"


@pytest.mark.asyncio
async def test_a_refresh_touches_only_what_changed(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(
        fake_site,
        {
            "same": markdown_page("Same"),
            "edited": markdown_page("Edited"),
            "removed": markdown_page("Removed"),
        },
    )
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)
    before = harness.pages()
    first_check = harness.adapter.get_site(site_id, DB)
    assert first_check is not None
    # The edited page has a node in the graph, homed at it alone.
    graph = GraphRepository(harness.adapter.session, database_name=DB)
    graph.create_template(TemplateCreate(name="Topic", template_type="node"), custom_id="t")
    node = graph.create_node(
        NodeCreate(template_id="t", label="Old fact", source_id=before[_url("edited")]["id"])
    )
    harness.queue.calls.clear()

    new_body = markdown_page("Edited, now with more words")
    _serve(
        fake_site,
        {"same": markdown_page("Same"), "edited": new_body, "added": markdown_page("Added")},
    )
    result = await harness.run(site_id, refresh=True)

    after = harness.pages()
    assert sorted(after) == sorted(_url(n) for n in ("same", "edited", "removed", "added"))

    same = after[_url("same")]
    assert same["id"] == before[_url("same")]["id"]
    assert same["status"] == "committed" and same["site_gone_at"] is None

    edited = after[_url("edited")]
    assert edited["id"] == before[_url("edited")]["id"]  # re-imported in place
    assert edited["content_hash"] == hashlib.sha256(new_body.encode()).hexdigest()
    assert edited["status"] == "pending"
    assert _staged_text(edited["filepath"]) == new_body
    assert graph.get_node(node.id) is None  # its graph rows released

    removed = after[_url("removed")]
    assert removed["site_gone_at"] is not None  # marked, never deleted
    assert removed["status"] == "committed"

    added = after[_url("added")]
    assert added["site_id"] == site_id and added["status"] == "pending"

    queued = sorted(call["file_id"] for call in harness.queue.calls)
    assert queued == sorted([edited["id"], added["id"]])

    site = harness.adapter.get_site(site_id, DB)
    assert site is not None
    assert site["status"] == "imported"
    assert site["page_count"] == 3
    assert site["last_checked_at"] is not None
    assert first_check["last_checked_at"] is not None
    assert site["last_checked_at"] >= first_check["last_checked_at"]
    assert result["unchanged"] == 1
    assert result["updated"] == 1
    assert result["added"] == 1
    assert result["gone"] == 1


@pytest.mark.asyncio
async def test_a_gone_page_listed_again_is_no_longer_gone(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    pages = {"a": markdown_page("A"), "b": markdown_page("B")}
    _serve(fake_site, pages)
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)

    _serve(fake_site, {"a": pages["a"]})
    await harness.run(site_id, refresh=True)
    assert harness.pages()[_url("b")]["site_gone_at"] is not None

    _serve(fake_site, pages)
    result = await harness.run(site_id, refresh=True)
    assert harness.pages()[_url("b")]["site_gone_at"] is None
    assert result["unchanged"] == 2 and result["gone"] == 0


@pytest.mark.asyncio
async def test_a_page_still_processing_is_left_for_the_next_check(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"a": markdown_page("A")})
    site_id = harness.create_site()
    await harness.run(site_id)
    source = harness.pages()[_url("a")]
    harness.adapter.update_file(source["id"], DB, {"status": SourceStatus.EXTRACTING})
    harness.queue.calls.clear()

    _serve(fake_site, {"a": markdown_page("A, edited")})
    result = await harness.run(site_id, refresh=True)

    row = harness.pages()[_url("a")]
    assert row["status"] == "extracting" and row["content_hash"] == source["content_hash"]
    assert harness.queue.calls == []
    assert result["busy"] == 1


@pytest.mark.asyncio
async def test_a_page_that_cannot_be_fetched_on_a_refresh_keeps_its_content(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"a": markdown_page("A")})
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)
    source = harness.pages()[_url("a")]

    _serve(fake_site, {"a": markdown_page("A")})
    fake_site.add(_url("a"), "down", content_type="text/plain", status=503)
    result = await harness.run(site_id, refresh=True)

    row = harness.pages()[_url("a")]
    assert row["status"] == "committed" and row["content_hash"] == source["content_hash"]
    assert result["unreachable"] == 1


@pytest.mark.asyncio
async def test_a_capped_or_empty_discovery_marks_nothing_gone(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"a": markdown_page("A"), "b": markdown_page("B")})
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)

    _serve(fake_site, {})
    fake_site.routes.clear()  # the site is down: nothing discoverable
    result = await harness.run(site_id, refresh=True)

    assert result["status"] == "error"
    assert all(row["site_gone_at"] is None for row in harness.pages().values())
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["status"] == "failed"


def _serve_sitemap(site: FakeSite, names: list[str]) -> None:
    """The same site without llms.txt: a sitemap of its HTML pages (no ``.md`` twins)."""
    site.routes.clear()
    site.page(ROOT, "Example Docs", [])
    locs = "".join(f"<url><loc>{ROOT}/{name}</loc></url>" for name in names)
    site.add(
        f"{ROOT}/sitemap.xml",
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>',
        content_type="application/xml",
    )
    for name in names:
        site.add(
            f"{ROOT}/{name}",
            f"<html><head><title>{name}</title></head><body><article><h1>{name}</h1>"
            f"<p>{BODY}{name}.</p></article></body></html>",
        )


@pytest.mark.asyncio
async def test_a_change_of_discovery_method_neither_duplicates_nor_marks_gone(
    harness: SiteHarness, fake_site: FakeSite
) -> None:
    """llms.txt links ``/a.md``, a sitemap lists ``/a``: the same page, matched, not added."""
    _serve(fake_site, {"intro": markdown_page("Intro"), "install": markdown_page("Install")})
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)
    before = harness.pages()

    # The site now has only a sitemap, which no longer lists the install page.
    _serve_sitemap(fake_site, ["intro", "new"])
    result = await harness.run(site_id, refresh=True)

    after = harness.pages()
    assert sorted(after) == [_url("install"), _url("intro"), f"{ROOT}/new"]
    assert after[_url("intro")]["id"] == before[_url("intro")]["id"]
    # A run whose method differs from the stored one marks nothing gone.
    assert all(row["site_gone_at"] is None for row in after.values())
    assert result["added"] == 1 and result["gone"] == 0
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["discovery_method"] == "sitemap"

    # The next check by the same method may mark the unlisted page gone.
    _settle(harness)
    again = await harness.run(site_id, refresh=True)
    assert harness.pages()[_url("install")]["site_gone_at"] is not None
    assert again["gone"] == 1


@pytest.mark.asyncio
async def test_a_page_deleted_during_a_refresh_fails_only_itself(
    harness: SiteHarness, fake_site: FakeSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.exceptions import NotFoundError
    from chaoscypher_core.operations.sources import site_import_handler

    _serve(fake_site, {"a": markdown_page("A"), "b": markdown_page("B")})
    site_id = harness.create_site()
    await harness.run(site_id)
    _settle(harness)
    gone_id = harness.pages()[_url("a")]["id"]
    real_reimport = site_import_handler.reimport_page_in_place

    async def _deleted_meanwhile(source_id: str, *args: Any, **kwargs: Any) -> None:
        if source_id == gone_id:
            raise NotFoundError("Source", source_id)
        await real_reimport(source_id, *args, **kwargs)

    monkeypatch.setattr(site_import_handler, "reimport_page_in_place", _deleted_meanwhile)
    _serve(fake_site, {"a": markdown_page("A, edited"), "b": markdown_page("B, edited")})

    result = await harness.run(site_id, refresh=True)

    assert result["status"] == "ok"
    assert result["updated"] == 1 and result["failed"] == 1
    assert harness.pages()[_url("b")]["status"] == "pending"
    site = harness.adapter.get_site(site_id, DB)
    assert site is not None and site["status"] == "imported"
