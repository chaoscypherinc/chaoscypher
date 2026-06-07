# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the new per-source entity/relationship adapter methods.

Migration 0042 replaced the heavy ``sources.extraction_results`` JSON
column with two dedicated tables. These tests cover the
``replace_source_extraction``, ``get_source_entities_page``,
``list_source_entities``, ``get_source_relationships_page``, and
``list_source_relationships`` helpers added to ``SourcesMixin``.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _seed_source(adapter: SqliteAdapter, source_id: str, tmp_path: Path) -> None:
    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename=f"{source_id}.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )


def test_replace_source_extraction_inserts_rows(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_source(adapter, "src_a", tmp_path)
    entities = [
        {"name": "Alpha", "type": "Concept", "confidence": 0.9},
        {"name": "Beta", "type": "Concept", "confidence": 0.5},
    ]
    relationships = [
        {"source": 0, "target": 1, "type": "related_to", "confidence": 0.8},
    ]

    adapter.replace_source_extraction(
        source_id="src_a",
        database_name="default",
        entities=entities,
        relationships=relationships,
    )

    loaded = adapter.list_source_entities("src_a", "default")
    assert [e["name"] for e in loaded] == ["Alpha", "Beta"]
    assert all(e["id"].startswith("ent_") for e in loaded)

    rels = adapter.list_source_relationships("src_a", "default")
    assert len(rels) == 1
    assert rels[0]["from"] == "Alpha"
    assert rels[0]["to"] == "Beta"
    assert rels[0]["predicate"] == "related_to"
    assert rels[0]["confidence"] == 0.8


def test_replace_source_extraction_drops_out_of_range_relationships(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_source(adapter, "src_b", tmp_path)
    adapter.replace_source_extraction(
        source_id="src_b",
        database_name="default",
        entities=[{"name": "Only", "type": "Concept"}],
        relationships=[
            {"source": 0, "target": 99, "type": "bad"},
            {"source": "string", "target": 0, "type": "bad"},
        ],
    )

    assert adapter.list_source_relationships("src_b", "default") == []


def test_replace_source_extraction_wipes_previous_rows(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_source(adapter, "src_c", tmp_path)
    adapter.replace_source_extraction(
        source_id="src_c",
        database_name="default",
        entities=[{"name": "First", "type": "T"}],
        relationships=[],
    )
    adapter.replace_source_extraction(
        source_id="src_c",
        database_name="default",
        entities=[{"name": "Second", "type": "T"}],
        relationships=[],
    )

    rows = adapter.list_source_entities("src_c", "default")
    assert [e["name"] for e in rows] == ["Second"]


def test_get_source_entities_page_default_sort(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_source(adapter, "src_d", tmp_path)
    adapter.replace_source_extraction(
        source_id="src_d",
        database_name="default",
        entities=[
            {"name": "a", "confidence": 0.1},
            {"name": "b", "confidence": 0.3},
            {"name": "c", "confidence": 0.2},
        ],
        relationships=[],
    )

    page1 = adapter.get_source_entities_page(
        "src_d", "default", page=1, per_page=2, sort_by="default", sort_order="desc"
    )
    assert page1["total"] == 3
    # default sort returns the first two entities by ordinal (descending
    # because tests pass sort_order="desc"); ordinal 2 then 1.
    assert [e["name"] for e in page1["entities"]] == ["c", "b"]


def test_get_source_entities_page_sort_by_confidence(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_source(adapter, "src_e", tmp_path)
    adapter.replace_source_extraction(
        source_id="src_e",
        database_name="default",
        entities=[
            {"name": "low", "confidence": 0.1},
            {"name": "high", "confidence": 0.9},
            {"name": "mid", "confidence": 0.5},
        ],
        relationships=[],
    )

    page = adapter.get_source_entities_page(
        "src_e",
        "default",
        page=1,
        per_page=3,
        sort_by="confidence",
        sort_order="desc",
    )
    assert [e["name"] for e in page["entities"]] == ["high", "mid", "low"]


def test_get_source_entities_page_sort_by_name(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_source(adapter, "src_f", tmp_path)
    adapter.replace_source_extraction(
        source_id="src_f",
        database_name="default",
        entities=[
            {"name": "Zeta"},
            {"name": "alpha"},
            {"name": "Mu"},
        ],
        relationships=[],
    )

    page = adapter.get_source_entities_page(
        "src_f",
        "default",
        page=1,
        per_page=3,
        sort_by="name",
        sort_order="asc",
    )
    # case-insensitive lower() based sort: alpha, Mu, Zeta
    assert [e["name"] for e in page["entities"]] == ["alpha", "Mu", "Zeta"]


def test_get_source_relationships_page_joins_entity_names(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_source(adapter, "src_g", tmp_path)
    adapter.replace_source_extraction(
        source_id="src_g",
        database_name="default",
        entities=[
            {"name": "Alice"},
            {"name": "Bob"},
        ],
        relationships=[
            {"source": 0, "target": 1, "type": "knows", "confidence": 0.8},
        ],
    )

    page = adapter.get_source_relationships_page("src_g", "default", page=1, per_page=10)
    assert page["total"] == 1
    rel = page["relationships"][0]
    assert rel["from"] == "Alice"
    assert rel["to"] == "Bob"
    assert rel["predicate"] == "knows"


def test_get_source_extraction_metadata_returns_domain_and_log(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_source(adapter, "src_h", tmp_path)
    adapter.complete_extraction(
        source_id="src_h",
        entities=[{"name": "E"}],
        relationships=[],
        detected_domain="technical",
        cross_chunk_filtering_log={"stages": [{"stage": "dedup", "removed_count": 1}]},
    )
    metadata = adapter.get_source_extraction_metadata("src_h", "default")
    assert metadata is not None
    assert metadata["extraction_domain"] == "technical"
    assert metadata["cross_chunk_filtering_log"] == {
        "stages": [{"stage": "dedup", "removed_count": 1}]
    }
