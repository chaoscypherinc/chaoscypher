# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A package's source keywords come back as tags through the one tag rule.

A package exported before the tag rule can carry keywords that differ only in
case, or that break the rule. Importing it normalises the first kind into one
tag and names the second in the import's warnings; the import itself succeeds.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow, SourceTag, SourceTagAssignment
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.repo_factories.graph_factory import get_transaction_graph_repository
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions
from chaoscypher_core.services.package.importer.models import ImportStats
from chaoscypher_core.services.sources.tags import get_or_create_tags


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "restore-tags"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _seed_old_style_tags(adapter: SqliteAdapter) -> None:
    """A source whose stored tag names predate the rule (written straight to the table)."""
    session = adapter.session
    assert session is not None
    session.add(
        SourceRow(
            id="doc-a",
            database_name="default",
            filename="doc-a.txt",
            filepath="",
            title="Doc A",
            source_type="text",
            status="committed",
            full_text="Some text.",
        )
    )
    session.flush()
    for i, name in enumerate(("Work", "WORK", "Bad Tag")):
        session.add(SourceTag(id=f"tag-{i}", database_name="default", name=name))
    session.flush()
    for i in range(3):
        session.add(
            SourceTagAssignment(
                id=f"asg-{i}", source_id="doc-a", tag_id=f"tag-{i}", database_name="default"
            )
        )
    session.commit()


@pytest.mark.asyncio
async def test_old_package_tags_normalise_and_invalid_ones_warn(adapter: SqliteAdapter) -> None:
    _seed_old_style_tags(adapter)
    data = CcxExporter(
        graph_repository=get_transaction_graph_repository(adapter, "default"),
        sources_repository=adapter,
        settings=build_engine_settings(get_settings()),
    ).export(include_embeddings=False, source_ids=["doc-a"])

    importer = CcxImporter(
        graph_repository=get_transaction_graph_repository(adapter, "imported"),
        sources_repository=adapter,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name="imported"))

    assert stats.is_success, stats.errors
    source = adapter.get_source_by_ccx_iri("urn:ccx:chaoscypher:source/doc-a", "imported")
    assert source is not None
    assert [t["name"] for t in adapter.get_source_tags(source["id"])] == ["work"]
    assert [t["name"] for t in adapter.list_tags("imported")] == ["work"]
    tag_warnings = [w for w in stats.warnings if "tag" in w]
    assert len(tag_warnings) == 1, stats.warnings
    assert 'tag "Bad Tag" is not allowed' in tag_warnings[0]
    assert tag_warnings[0].startswith('source "Doc A": ')
    assert tag_warnings[0].endswith("; the tag was skipped")


def test_keywords_past_the_cap_keep_the_first_32_and_warn(adapter: SqliteAdapter) -> None:
    _seed_old_style_tags(adapter)
    importer = CcxImporter(
        graph_repository=get_transaction_graph_repository(adapter, "default"),
        sources_repository=adapter,
    )
    adapter.set_source_tags("doc-a", [], "default")  # no local tags
    stats = ImportStats()
    keywords = [f"k{i:02d}" for i in range(34)]

    importer._restore_source_tags("doc-a", keywords, "default", stats)

    kept = sorted(t["name"] for t in adapter.get_source_tags("doc-a"))
    assert kept == keywords[:32]
    assert stats.warnings == [
        'source "Doc A": a document can carry at most 32 tags; this would give it 34; '
        "skipped: k32, k33"
    ]


def test_a_reimport_caps_the_union_with_the_local_tags(adapter: SqliteAdapter) -> None:
    """31 local tags plus 3 new keywords: the first new one fits, the other two are named."""
    _seed_old_style_tags(adapter)
    importer = CcxImporter(
        graph_repository=get_transaction_graph_repository(adapter, "default"),
        sources_repository=adapter,
    )
    local = [f"local-{i:02d}" for i in range(31)]
    adapter.set_source_tags(
        "doc-a", [t["id"] for t in get_or_create_tags(adapter, "default", local)], "default"
    )
    stats = ImportStats()

    importer._restore_source_tags(
        "doc-a", [*local[:5], "new-a", "new-b", "new-c"], "default", stats
    )

    names = sorted(t["name"] for t in adapter.get_source_tags("doc-a"))
    assert names == sorted([*local, "new-a"])
    assert stats.warnings == [
        'source "Doc A": a document can carry at most 32 tags; this would give it 34; '
        "skipped: new-b, new-c"
    ]
