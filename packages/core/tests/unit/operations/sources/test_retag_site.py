# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``retag_site``: a site's stored tags and every page's, changed together or not at all.

Real migrated database; the site's pages are source rows naming it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.exceptions import ConflictError
from chaoscypher_core.models import SiteStatus, SourceStatus
from chaoscypher_core.operations.sources.site_runs import retag_site
from chaoscypher_core.services.sources.tags import (
    TagRuleError,
    edit_source_tags,
    source_tag_names,
)


DB = "default"
SITE = "site-a"
ROOT = "https://docs.example.com"


@pytest.fixture
def adapter(tmp_path: Path) -> Iterator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    upgrade_to_head(db_path)
    built = SqliteAdapter(str(db_path), database_name=DB)
    built.connect()
    yield built
    built.disconnect()
    evict_engine(db_path)


def _site_with_pages(adapter: SqliteAdapter, *, status: str = SiteStatus.IMPORTED) -> list[str]:
    """A site tagged ``alpha`` with two pages tagged ``alpha``; the pages' ids."""
    adapter.create_site(
        site_id=SITE,
        database_name=DB,
        url=f"{ROOT}/",
        title="Example Docs",
        status=status,
        options={"tags": ["alpha"]},
    )
    ids = []
    for name in ("intro", "install"):
        page_id = f"page-{name}"
        adapter.create_source(
            {
                "id": page_id,
                "database_name": DB,
                "filename": f"{name}.md",
                "filepath": f"/pages/{name}.md",
                "file_type": "md",
                "file_size": 10,
                "status": SourceStatus.INDEXED,
                "source_type": "url",
                "origin_url": f"{ROOT}/{name}.md",
                "site_id": SITE,
            }
        )
        edit_source_tags(adapter, DB, page_id, add=["alpha"])
        ids.append(page_id)
    return ids


def _site_tags(adapter: SqliteAdapter) -> list[str]:
    site = adapter.get_site(SITE, DB)
    assert site is not None and site["options"] is not None
    return list(site["options"]["tags"])


def test_retag_site_adds_to_the_site_and_every_page(adapter: SqliteAdapter) -> None:
    pages = _site_with_pages(adapter)

    after = retag_site(adapter, SITE, DB, tags=["Beta"], replace_tags=False)

    assert after == ["alpha", "beta"]
    assert _site_tags(adapter) == ["alpha", "beta"]
    assert all(source_tag_names(adapter, p) == ["alpha", "beta"] for p in pages)


def test_retag_site_with_replace_tags_makes_them_the_whole_set(adapter: SqliteAdapter) -> None:
    pages = _site_with_pages(adapter)

    after = retag_site(adapter, SITE, DB, tags=["gamma"], replace_tags=True)

    assert after == ["gamma"]
    assert _site_tags(adapter) == ["gamma"]
    assert all(source_tag_names(adapter, p) == ["gamma"] for p in pages)


def test_a_retag_that_puts_one_page_over_the_cap_changes_nothing(
    adapter: SqliteAdapter,
) -> None:
    intro, install = _site_with_pages(adapter)
    full = [f"t{i}" for i in range(31)]
    edit_source_tags(adapter, DB, install, add=full)  # 32 tags: alpha and 31 more

    with pytest.raises(TagRuleError, match="at most 32 tags; this would give it 33"):
        retag_site(adapter, SITE, DB, tags=["beta"], replace_tags=False)

    assert _site_tags(adapter) == ["alpha"]
    assert source_tag_names(adapter, intro) == ["alpha"]
    assert source_tag_names(adapter, install) == sorted(["alpha", *full])


def test_retag_site_is_refused_while_a_run_is_live(adapter: SqliteAdapter) -> None:
    """A live run read the site's options when it started: its later pages would miss the tags."""
    pages = _site_with_pages(adapter, status=SiteStatus.REFRESHING)

    with pytest.raises(ConflictError, match="adding tags must wait"):
        retag_site(adapter, SITE, DB, tags=["beta"], replace_tags=False)

    assert _site_tags(adapter) == ["alpha"]
    assert all(source_tag_names(adapter, p) == ["alpha"] for p in pages)
