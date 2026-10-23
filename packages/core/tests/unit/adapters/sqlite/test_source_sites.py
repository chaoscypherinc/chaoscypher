# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``SourceSitesMixin``: the ``source_sites`` rows and the pages that name them (migration 0021)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from chaoscypher_core.models import SiteStatus, SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


DB = "default"


def _site(adapter: SqliteAdapter, site_id: str = "site-1") -> None:
    adapter.create_site(
        site_id=site_id,
        database_name=DB,
        url="https://docs.example.com",
        title="docs.example.com",
        status=SiteStatus.QUEUED,
        options={"extraction_depth": "quick"},
    )


def _page(adapter: SqliteAdapter, tmp_path: Path, source_id: str, url: str, site: str) -> None:
    adapter.upload_source(
        source_id=source_id,
        database_name=DB,
        filename=f"{source_id}.md",
        file_content=b"# page\n",
        staging_dir=str(tmp_path / "sources"),
        origin_url=url,
        source_type_override="webpage",
        content_hash="h-" + source_id,
        site_id=site,
    )


def test_a_site_round_trips(sqlite_adapter: SqliteAdapter) -> None:
    _site(sqlite_adapter)
    site = sqlite_adapter.get_site("site-1", DB)
    assert site is not None
    assert site["url"] == "https://docs.example.com"
    assert site["status"] == "queued"
    assert site["page_count"] == 0
    assert site["options"] == {"extraction_depth": "quick"}
    assert site["discovery_method"] is None and site["last_checked_at"] is None
    assert sqlite_adapter.get_site("site-1", "other") is None

    checked = datetime.now(UTC)
    sqlite_adapter.update_site(
        "site-1",
        DB,
        {"status": SiteStatus.IMPORTED, "page_count": 5, "last_checked_at": checked},
    )
    updated = sqlite_adapter.get_site("site-1", DB)
    assert updated is not None
    assert updated["status"] == "imported" and updated["page_count"] == 5
    assert updated["last_checked_at"] is not None


def test_sites_list_newest_first_with_a_total(sqlite_adapter: SqliteAdapter) -> None:
    for n in range(3):
        _site(sqlite_adapter, f"site-{n}")
    first, total = sqlite_adapter.list_sites(DB, page=1, page_size=2)
    second, _ = sqlite_adapter.list_sites(DB, page=2, page_size=2)
    assert total == 3
    assert [s["id"] for s in first + second] == ["site-2", "site-1", "site-0"]
    assert sqlite_adapter.list_sites("other", page=1, page_size=2) == ([], 0)


def test_pages_name_their_site_and_are_counted(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _site(sqlite_adapter)
    _site(sqlite_adapter, "site-2")
    _page(sqlite_adapter, tmp_path, "p1", "https://docs.example.com/a", "site-1")
    _page(sqlite_adapter, tmp_path, "p2", "https://docs.example.com/b", "site-1")
    _page(sqlite_adapter, tmp_path, "p3", "https://docs.example.com/c", "site-2")
    sqlite_adapter.update_file("p2", DB, {"status": SourceStatus.COMMITTED})

    pages = {p["id"]: p for p in sqlite_adapter.list_site_pages("site-1", DB)}
    assert set(pages) == {"p1", "p2"}
    assert pages["p1"]["origin_url"] == "https://docs.example.com/a"
    assert pages["p1"]["content_hash"] == "h-p1"
    assert pages["p1"]["auto_analyze"] is True
    assert pages["p1"]["site_gone_at"] is None

    assert sqlite_adapter.set_site_pages_gone(["p1"], DB, datetime.now(UTC)) == 1
    counts = sorted(
        (c["site_id"], c["status"], c["gone"], c["count"])
        for c in sqlite_adapter.count_site_pages(["site-1", "site-2"], DB)
    )
    assert counts == [
        ("site-1", "committed", False, 1),
        ("site-1", "pending", True, 1),
        ("site-2", "pending", False, 1),
    ]
    assert sqlite_adapter.set_site_pages_gone(["p1"], DB, None) == 1
    assert sqlite_adapter.list_site_pages("site-1", DB)[0]["site_gone_at"] is None


def test_deleting_a_site_keeps_its_pages_unlinked(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _site(sqlite_adapter)
    _page(sqlite_adapter, tmp_path, "p1", "https://docs.example.com/a", "site-1")
    assert sqlite_adapter.delete_site("site-1", DB) is True
    assert sqlite_adapter.delete_site("site-1", DB) is False
    source = sqlite_adapter.get_source("p1", DB)
    assert source is not None
    assert source["site_id"] is None


def test_restaging_a_page_rewrites_its_file_and_sends_it_back_to_pending(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _site(sqlite_adapter)
    _page(sqlite_adapter, tmp_path, "p1", "https://docs.example.com/a", "site-1")
    sqlite_adapter.update_file(
        "p1",
        DB,
        {"status": SourceStatus.INDEXED, "indexing_complete": True, "error_message": "x"},
    )
    payload = b"# page, edited\n"
    digest = hashlib.sha256(payload).hexdigest()

    sqlite_adapter.restage_site_page(
        "p1", DB, payload=payload, content_hash=digest, title="Edited page"
    )

    source = sqlite_adapter.get_source("p1", DB)
    assert source is not None
    assert Path(source["filepath"]).read_bytes() == payload
    assert source["content_hash"] == digest
    assert source["file_size"] == len(payload)
    assert source["title"] == "Edited page"
    assert source["status"] == "pending"
    assert source["indexing_complete"] is False
    assert source["error_message"] is None


def test_the_source_list_filters_by_site(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> None:
    """One site's pages, the sources outside any site, or the pages of every site.

    The web UI's sources list shows a site as one row: it lists the sources
    outside a site, and a site's pages when its row is expanded.
    """
    _site(sqlite_adapter)
    _site(sqlite_adapter, "site-2")
    _page(sqlite_adapter, tmp_path, "p1", "https://docs.example.com/a", "site-1")
    _page(sqlite_adapter, tmp_path, "p2", "https://docs.example.com/b", "site-1")
    _page(sqlite_adapter, tmp_path, "p3", "https://docs.example.com/c", "site-2")
    sqlite_adapter.upload_source(
        source_id="loose",
        database_name=DB,
        filename="loose.md",
        file_content=b"# loose\n",
        staging_dir=str(tmp_path / "sources"),
    )
    sqlite_adapter.set_site_pages_gone(["p2"], DB, datetime.now(UTC))

    def ids(site_id: str | None = None, in_site: bool | None = None) -> tuple[set[str], int]:
        sources, total = sqlite_adapter.list_sources(
            page=1, page_size=50, site_id=site_id, in_site=in_site
        )
        return {s["id"] for s in sources}, total

    assert ids(site_id="site-1") == ({"p1", "p2"}, 2)
    assert ids(in_site=False) == ({"loose"}, 1)
    assert ids(in_site=True) == ({"p1", "p2", "p3"}, 3)
    assert ids()[1] == 4

    listed = {s["id"]: s for s in sqlite_adapter.list_sources(site_id="site-1")[0]}
    assert listed["p1"]["site_id"] == "site-1"
    assert listed["p1"]["origin_url"] == "https://docs.example.com/a"
    assert listed["p1"]["site_gone_at"] is None
    assert listed["p2"]["site_gone_at"] is not None
