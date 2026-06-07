# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 2 — bulk citation clear methods on CitationStorageProtocol."""

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
    SourceCitation,
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


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
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
        }
    )


def test_clear_all_citations_returns_count(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    with adapter.transaction():
        for i in range(3):
            adapter.session.add(
                DocumentChunk(
                    id=f"chunk-{i}",
                    database_name="test",
                    source_id="src-1",
                    chunk_index=i,
                    content=f"c{i}",
                )
            )
    with adapter.transaction():
        for i in range(3):
            adapter.session.add(
                SourceCitation(
                    id=f"cit-{i}",
                    database_name="test",
                    entity_uri=f"chaoscypher:entity_{i}",
                    entity_label=f"Entity {i}",
                    source_id="src-1",
                    chunk_id=f"chunk-{i}",
                    confidence=0.9,
                    extraction_method="ai_extraction",
                )
            )
    assert adapter.clear_all_citations() == 3
    assert adapter.clear_all_citations() == 0


def test_delete_all_relationship_citations_returns_count(adapter: SqliteAdapter) -> None:
    _seed_source(adapter, "src-1")
    with adapter.transaction():
        for i in range(2):
            adapter.session.add(
                DocumentChunk(
                    id=f"chunk-{i}",
                    database_name="test",
                    source_id="src-1",
                    chunk_index=i,
                    content=f"c{i}",
                )
            )
    with adapter.transaction():
        for i in range(2):
            adapter.session.add(
                RelationshipCitation(
                    id=f"rc-{i}",
                    database_name="test",
                    edge_id=f"edge-{i}",
                    edge_label="worked_at",
                    source_entity_label="A",
                    target_entity_label="B",
                    source_id="src-1",
                    chunk_id=f"chunk-{i}",
                    extraction_method="ai_extraction",
                )
            )
    assert adapter.delete_all_relationship_citations() == 2


def test_clear_all_citations_empty_is_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_citations() == 0


def test_delete_all_relationship_citations_empty_is_noop(adapter: SqliteAdapter) -> None:
    assert adapter.delete_all_relationship_citations() == 0
