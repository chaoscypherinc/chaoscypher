# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for non-bulk SourceCitationsMixin methods.

Exercises entity/relationship citation CRUD, filtered listing, pagination,
source statistics aggregation, orphan detection, and per-source deletion
against a real file-backed SQLite database (the bulk clear path is covered
separately in ``test_sources_citations_bulk.py``).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    RelationshipCitation,
)


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed_source(adapter: SqliteAdapter, source_id: str = "src-1") -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "committed",
            "total_content_length": 1234,
        }
    )


def _add_chunk(adapter: SqliteAdapter, chunk_id: str, source_id: str = "src-1") -> None:
    with adapter.transaction():
        adapter.session.add(
            DocumentChunk(
                id=chunk_id,
                database_name="test",
                source_id=source_id,
                chunk_index=0,
                content="c",
                status="committed",
            )
        )


def _citation_dict(
    cid: str,
    *,
    source_id: str = "src-1",
    chunk_id: str = "chunk-1",
    entity_uri: str = "chaoscypher:entity_1",
    entity_label: str = "Entity One",
    entity_type: str = "Person",
    confidence: float = 0.9,
) -> dict:
    return {
        "id": cid,
        "database_name": "test",
        "entity_uri": entity_uri,
        "entity_label": entity_label,
        "entity_type": entity_type,
        "source_id": source_id,
        "chunk_id": chunk_id,
        "confidence": confidence,
        "extraction_method": "ai_extraction",
    }


# ---------------------------------------------------------------------------
# create_citation
# ---------------------------------------------------------------------------


def test_create_citation(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    created = adapter.create_citation(_citation_dict("cit-1"))
    assert created["id"] == "cit-1"
    assert created["entity_uri"] == "chaoscypher:entity_1"
    assert created["confidence"] == 0.9


# ---------------------------------------------------------------------------
# list_citations
# ---------------------------------------------------------------------------


def test_list_citations_filters(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    _seed_source(adapter, "src-2")
    _add_chunk(adapter, "chunk-1", "src-1")
    _add_chunk(adapter, "chunk-2", "src-2")
    adapter.create_citation(
        _citation_dict("cit-1", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:a")
    )
    adapter.create_citation(
        _citation_dict("cit-2", source_id="src-2", chunk_id="chunk-2", entity_uri="uri:b")
    )

    all_rows = adapter.list_citations("test")
    assert {r["id"] for r in all_rows} == {"cit-1", "cit-2"}

    by_uri = adapter.list_citations("test", entity_uri="uri:a")
    assert [r["id"] for r in by_uri] == ["cit-1"]

    by_source = adapter.list_citations("test", source_id="src-2")
    assert [r["id"] for r in by_source] == ["cit-2"]

    limited = adapter.list_citations("test", limit=1)
    assert len(limited) == 1


def test_list_citations_empty(adapter: SqliteAdapter) -> None:
    assert adapter.list_citations("test") == []


# ---------------------------------------------------------------------------
# get_citations_batch
# ---------------------------------------------------------------------------


def test_get_citations_batch(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    adapter.create_citation(_citation_dict("cit-1", entity_uri="uri:a"))
    adapter.create_citation(_citation_dict("cit-2", entity_uri="uri:b"))
    adapter.create_citation(_citation_dict("cit-3", entity_uri="uri:c"))

    rows = adapter.get_citations_batch("test", ["uri:a", "uri:c"])
    assert {r["entity_uri"] for r in rows} == {"uri:a", "uri:c"}


def test_get_citations_batch_source_filter(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    _seed_source(adapter, "src-2")
    _add_chunk(adapter, "chunk-1", "src-1")
    _add_chunk(adapter, "chunk-2", "src-2")
    adapter.create_citation(
        _citation_dict("cit-1", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:a")
    )
    adapter.create_citation(
        _citation_dict("cit-2", source_id="src-2", chunk_id="chunk-2", entity_uri="uri:a")
    )
    rows = adapter.get_citations_batch("test", ["uri:a"], source_ids=["src-1"])
    assert [r["id"] for r in rows] == ["cit-1"]


def test_get_citations_batch_empty_input(adapter: SqliteAdapter) -> None:
    assert adapter.get_citations_batch("test", []) == []


# ---------------------------------------------------------------------------
# get_citations_by_entity / get_citations_by_source (pagination)
# ---------------------------------------------------------------------------


def test_get_citations_by_entity_pagination(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    for i in range(3):
        adapter.create_citation(_citation_dict(f"cit-{i}", entity_uri="uri:shared"))
    rows, total = adapter.get_citations_by_entity("uri:shared", offset=0, limit=2)
    assert total == 3
    assert len(rows) == 2


def test_get_citations_by_entity_empty(adapter: SqliteAdapter) -> None:
    rows, total = adapter.get_citations_by_entity("uri:none")
    assert rows == []
    assert total == 0


def test_get_citations_by_source_pagination(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    for i in range(5):
        adapter.create_citation(_citation_dict(f"cit-{i}", entity_uri=f"uri:{i}"))
    page1, total = adapter.get_citations_by_source("src-1", page=1, page_size=2)
    assert total == 5
    assert len(page1) == 2
    page3, total = adapter.get_citations_by_source("src-1", page=3, page_size=2)
    assert len(page3) == 1


def test_get_citations_by_source_empty(adapter: SqliteAdapter) -> None:
    rows, total = adapter.get_citations_by_source("src-none")
    assert rows == []
    assert total == 0


# ---------------------------------------------------------------------------
# get_source_stats
# ---------------------------------------------------------------------------


def test_get_source_stats_missing_source(adapter: SqliteAdapter) -> None:
    stats = adapter.get_source_stats("nonexistent")
    assert stats["total_chunks"] == 0
    assert stats["total_citations"] == 0
    assert stats["entity_count"] == 0


def test_get_source_stats_full(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    # Chunks across statuses.
    with adapter.transaction():
        adapter.session.add(
            DocumentChunk(
                id="ch-c",
                database_name="test",
                source_id="src-1",
                chunk_index=0,
                content="x",
                status="committed",
            )
        )
        adapter.session.add(
            DocumentChunk(
                id="ch-s",
                database_name="test",
                source_id="src-1",
                chunk_index=1,
                content="y",
                status="staged",
            )
        )
        adapter.session.add(
            DocumentChunk(
                id="ch-r",
                database_name="test",
                source_id="src-1",
                chunk_index=2,
                content="z",
                status="rejected",
            )
        )
    # Entity citations.
    adapter.create_citation(
        _citation_dict(
            "cit-1",
            chunk_id="ch-c",
            entity_uri="uri:a",
            entity_label="Alice",
            entity_type="Person",
            confidence=0.8,
        )
    )
    adapter.create_citation(
        _citation_dict(
            "cit-2",
            chunk_id="ch-c",
            entity_uri="uri:b",
            entity_label="Acme",
            entity_type="Organization",
            confidence=1.0,
        )
    )
    # Relationship citation.
    with adapter.transaction():
        adapter.session.add(
            RelationshipCitation(
                id="rc-1",
                database_name="test",
                edge_id="edge-1",
                edge_label="works_at",
                source_entity_label="Alice",
                target_entity_label="Acme",
                source_id="src-1",
                chunk_id="ch-c",
                extraction_method="ai_extraction",
            )
        )

    stats = adapter.get_source_stats("src-1")
    assert stats["total_chunks"] == 3
    assert stats["committed_chunks"] == 1
    assert stats["staged_chunks"] == 1
    assert stats["rejected_chunks"] == 1
    assert stats["total_content_length"] == 1234
    assert stats["total_citations"] == 2
    assert stats["entity_count"] == 2
    assert stats["relationship_count"] == 1
    assert stats["entity_type_distribution"] == {"Person": 1, "Organization": 1}
    assert stats["relationship_type_distribution"] == {"works_at": 1}
    assert stats["avg_confidence"] == 0.9
    assert len(stats["top_entities"]) == 2


# ---------------------------------------------------------------------------
# create_relationship_citations_batch (non-clear paths)
# ---------------------------------------------------------------------------


def test_create_relationship_citations_batch_new_and_idempotent(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    payload = [
        {
            "id": "rc-1",
            "database_name": "test",
            "edge_id": "edge-1",
            "edge_label": "works_at",
            "source_entity_label": "A",
            "target_entity_label": "B",
            "source_id": "src-1",
            "chunk_id": "chunk-1",
            "extraction_method": "ai_extraction",
        }
    ]
    first = adapter.create_relationship_citations_batch(payload)
    assert len(first) == 1
    assert first[0]["id"] == "rc-1"
    # Re-dispatch with the same stable ID returns the existing row, no PK error.
    second = adapter.create_relationship_citations_batch([dict(payload[0])])
    assert len(second) == 1
    assert second[0]["id"] == "rc-1"


def test_create_relationship_citations_batch_generates_id(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    payload = [
        {
            "database_name": "test",
            "edge_id": "edge-9",
            "edge_label": "knows",
            "source_entity_label": "A",
            "target_entity_label": "B",
            "source_id": "src-1",
            "chunk_id": "chunk-1",
            "extraction_method": "ai_extraction",
        }
    ]
    rows = adapter.create_relationship_citations_batch(payload)
    assert len(rows) == 1
    assert rows[0]["id"]  # legacy caller got a UUID


def test_create_relationship_citations_batch_empty(adapter: SqliteAdapter) -> None:
    assert adapter.create_relationship_citations_batch([]) == []


def test_create_relationship_citations_batch_intra_batch_dup(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    row = {
        "id": "rc-dup",
        "database_name": "test",
        "edge_id": "edge-1",
        "edge_label": "works_at",
        "source_entity_label": "A",
        "target_entity_label": "B",
        "source_id": "src-1",
        "chunk_id": "chunk-1",
        "extraction_method": "ai_extraction",
    }
    rows = adapter.create_relationship_citations_batch([row, dict(row)])
    assert len(rows) == 2  # both result entries reference the single inserted row
    assert all(r["id"] == "rc-dup" for r in rows)


# ---------------------------------------------------------------------------
# clear_all / delete_citations_by_source / delete_citations_for_source
# ---------------------------------------------------------------------------


def test_clear_all_counts(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    adapter.create_citation(_citation_dict("cit-1"))
    with adapter.transaction():
        adapter.session.add(
            RelationshipCitation(
                id="rc-1",
                database_name="test",
                edge_id="edge-1",
                edge_label="works_at",
                source_entity_label="A",
                target_entity_label="B",
                source_id="src-1",
                chunk_id="chunk-1",
                extraction_method="ai_extraction",
            )
        )
    result = adapter.clear_all("test")
    assert result["sources_deleted"] == 1
    assert result["chunks_deleted"] == 1
    assert result["citations_deleted"] == 1
    assert result["relationship_citations_deleted"] == 1
    # Idempotent second clear yields all zeros.
    again = adapter.clear_all("test")
    assert again["sources_deleted"] == 0


def test_delete_citations_by_source(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    adapter.create_citation(_citation_dict("cit-1"))
    with adapter.transaction():
        adapter.session.add(
            RelationshipCitation(
                id="rc-1",
                database_name="test",
                edge_id="edge-1",
                edge_label="works_at",
                source_entity_label="A",
                target_entity_label="B",
                source_id="src-1",
                chunk_id="chunk-1",
                extraction_method="ai_extraction",
            )
        )
    result = adapter.delete_citations_by_source("src-1")
    assert result["entity_citations_deleted"] == 1
    assert result["relationship_citations_deleted"] == 1
    assert adapter.list_citations("test") == []


def test_delete_citations_for_source(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    _add_chunk(adapter, "chunk-1")
    adapter.create_citation(_citation_dict("cit-1"))
    adapter.delete_citations_for_source("src-1")
    assert adapter.list_citations("test") == []


# ---------------------------------------------------------------------------
# Orphan detection + entity-uri lookups
# ---------------------------------------------------------------------------


def test_get_orphaned_entity_uris(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    _seed_source(adapter, "src-2")
    _add_chunk(adapter, "chunk-1", "src-1")
    _add_chunk(adapter, "chunk-2", "src-2")
    # uri:shared cited by both sources -> not orphaned.
    adapter.create_citation(
        _citation_dict("c1", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:shared")
    )
    adapter.create_citation(
        _citation_dict("c2", source_id="src-2", chunk_id="chunk-2", entity_uri="uri:shared")
    )
    # uri:only1 cited only by src-1 -> orphaned when src-1 deleted.
    adapter.create_citation(
        _citation_dict("c3", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:only1")
    )
    orphans = adapter.get_orphaned_entity_uris("src-1")
    assert orphans == ["uri:only1"]


def test_get_orphaned_entity_uris_none(adapter: SqliteAdapter) -> None:
    _seed_source(adapter)
    assert adapter.get_orphaned_entity_uris("src-1") == []


def test_get_entity_uris_for_sources(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    _seed_source(adapter, "src-2")
    _add_chunk(adapter, "chunk-1", "src-1")
    _add_chunk(adapter, "chunk-2", "src-2")
    adapter.create_citation(
        _citation_dict("c1", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:a")
    )
    adapter.create_citation(
        _citation_dict("c2", source_id="src-2", chunk_id="chunk-2", entity_uri="uri:b")
    )
    # Duplicate URI from same source should dedupe.
    adapter.create_citation(
        _citation_dict("c3", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:a")
    )
    uris = adapter.get_entity_uris_for_sources(["src-1", "src-2"])
    assert set(uris) == {"uri:a", "uri:b"}


def test_get_entity_uris_for_sources_empty(adapter: SqliteAdapter) -> None:
    assert adapter.get_entity_uris_for_sources([]) == []


def test_get_entity_uris_grouped_by_source(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    _seed_source(adapter, "src-2")
    _add_chunk(adapter, "chunk-1", "src-1")
    _add_chunk(adapter, "chunk-2", "src-2")
    adapter.create_citation(
        _citation_dict("c1", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:a")
    )
    adapter.create_citation(
        _citation_dict("c2", source_id="src-1", chunk_id="chunk-1", entity_uri="uri:b")
    )
    adapter.create_citation(
        _citation_dict("c3", source_id="src-2", chunk_id="chunk-2", entity_uri="uri:c")
    )
    grouped = adapter.get_entity_uris_grouped_by_source("test", ["src-1", "src-2"])
    assert set(grouped["src-1"]) == {"uri:a", "uri:b"}
    assert grouped["src-2"] == ["uri:c"]


def test_get_entity_uris_grouped_by_source_empty(adapter: SqliteAdapter) -> None:
    assert adapter.get_entity_uris_grouped_by_source("test", []) == {}
