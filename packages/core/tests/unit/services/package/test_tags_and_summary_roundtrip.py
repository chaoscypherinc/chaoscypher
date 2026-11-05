# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A package carries a document's tags (``keywords``) and summary (``abstract``).

Export writes the summary as the schema.org ``abstract`` beside the tags'
``keywords``; import puts it back through the one summary rule. A summary the
rule refuses is named in the import's warnings and the import goes on. A
package that carries a summary replaces the local one on re-import, as any
other incoming source column does; one that carries none keeps it.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.repo_factories.graph_factory import get_transaction_graph_repository
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.export.ccx_mapping import source_records
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions
from chaoscypher_core.services.package.importer.models import ImportStats
from chaoscypher_core.services.sources.tags import get_or_create_tags


_IRI = "urn:ccx:chaoscypher:source/doc-a"


def _open(tmp_path: Path, name: str) -> SqliteAdapter:
    db_dir = tmp_path / name
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    return adapter


@pytest.fixture
def db_a(tmp_path: Path) -> Generator[SqliteAdapter]:
    adapter = _open(tmp_path, "db-a")
    yield adapter
    adapter.disconnect()


@pytest.fixture
def db_b(tmp_path: Path) -> Generator[SqliteAdapter]:
    adapter = _open(tmp_path, "db-b")
    yield adapter
    adapter.disconnect()


def _seed_tagged_source(adapter: SqliteAdapter) -> None:
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
            summary="A short note.",
        )
    )
    session.commit()
    tags = get_or_create_tags(adapter, "default", ["alpha", "beta"])
    adapter.add_source_tags("doc-a", [t["id"] for t in tags], "default")


def _export(adapter: SqliteAdapter) -> bytes:
    return CcxExporter(
        graph_repository=get_transaction_graph_repository(adapter, "default"),
        sources_repository=adapter,
        settings=build_engine_settings(get_settings()),
    ).export(include_embeddings=False, source_ids=["doc-a"])


async def _import(adapter: SqliteAdapter, data: bytes) -> ImportStats:
    importer = CcxImporter(
        graph_repository=get_transaction_graph_repository(adapter, "default"),
        sources_repository=adapter,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name="default"))
    assert stats.is_success, stats.errors
    return stats


def _tag_names(adapter: SqliteAdapter, source_id: str) -> list[str]:
    return sorted(t["name"] for t in adapter.get_source_tags(source_id))


@pytest.mark.asyncio
async def test_tags_and_summary_survive_export_and_import(
    db_a: SqliteAdapter, db_b: SqliteAdapter
) -> None:
    _seed_tagged_source(db_a)

    await _import(db_b, _export(db_a))

    source = db_b.get_source_by_ccx_iri(_IRI, "default")
    assert source is not None
    assert _tag_names(db_b, source["id"]) == ["alpha", "beta"]
    assert source["summary"] == "A short note."


@pytest.mark.asyncio
async def test_reimport_does_not_duplicate_tags(db_a: SqliteAdapter, db_b: SqliteAdapter) -> None:
    _seed_tagged_source(db_a)
    data = _export(db_a)
    await _import(db_b, data)
    source = db_b.get_source_by_ccx_iri(_IRI, "default")
    assert source is not None

    await _import(db_b, data)

    again = db_b.get_source_by_ccx_iri(_IRI, "default")
    assert again is not None
    assert again["id"] == source["id"]
    assert _tag_names(db_b, again["id"]) == ["alpha", "beta"]
    assert sorted(t["name"] for t in db_b.list_tags("default")) == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_reimport_replaces_local_summary_with_package_abstract(
    db_a: SqliteAdapter, db_b: SqliteAdapter
) -> None:
    _seed_tagged_source(db_a)
    data = _export(db_a)
    await _import(db_b, data)
    source = db_b.get_source_by_ccx_iri(_IRI, "default")
    assert source is not None
    db_b.update_source(source["id"], {"summary": "A local note."})

    await _import(db_b, data)

    again = db_b.get_source_by_ccx_iri(_IRI, "default")
    assert again is not None
    # The package's summary replaces the local one, as its other columns do.
    assert again["summary"] == "A short note."


def _importer(adapter: SqliteAdapter) -> CcxImporter:
    return CcxImporter(
        graph_repository=get_transaction_graph_repository(adapter, "default"),
        sources_repository=adapter,
    )


def test_reimport_without_a_usable_abstract_keeps_the_local_summary(db_b: SqliteAdapter) -> None:
    importer = _importer(db_b)
    record = {"@id": _IRI, "@type": "ccx:Source", "title": "Doc A"}
    source_id, _ = importer._upsert_source(None, record, "default", ImportStats())
    db_b.update_source(source_id, {"summary": "A local note."})

    stats = ImportStats()
    importer._upsert_source(None, record, "default", stats)
    importer._upsert_source(None, {**record, "abstract": "x" * 301}, "default", stats)

    source = db_b.get_source(source_id, "default")
    assert source is not None
    assert source["summary"] == "A local note."
    assert stats.warnings == [
        'source "Doc A": its summary was skipped: summary is too long: at most 300 characters'
    ]


def test_an_over_long_abstract_is_skipped_with_a_warning(db_b: SqliteAdapter) -> None:
    stats = ImportStats()
    src_rec = {"@id": _IRI, "@type": "ccx:Source", "title": "Doc A", "abstract": "x" * 301}

    source_id, _ = _importer(db_b)._upsert_source(None, src_rec, "default", stats)

    source = db_b.get_source(source_id, "default")
    assert source is not None
    assert source["summary"] is None
    assert stats.warnings == [
        'source "Doc A": its summary was skipped: summary is too long: at most 300 characters'
    ]


def test_an_abstract_that_is_not_text_is_skipped_with_a_warning(db_b: SqliteAdapter) -> None:
    stats = ImportStats()
    src_rec = {"@id": _IRI, "@type": "ccx:Source", "title": "Doc A", "abstract": 42}

    source_id, _ = _importer(db_b)._upsert_source(None, src_rec, "default", stats)

    source = db_b.get_source(source_id, "default")
    assert source is not None
    assert source["summary"] is None
    assert stats.warnings == ['source "Doc A": its summary was skipped: a summary is text, not int']


def test_a_source_without_a_summary_exports_no_abstract_key() -> None:
    for summary in (None, ""):
        record = source_records({"id": "doc-a", "title": "Doc A", "summary": summary}, [], None)[0]
        assert "abstract" not in record
    record = source_records({"id": "doc-a", "summary": "A short note."}, [], None)[0]
    assert record["abstract"] == "A short note."
