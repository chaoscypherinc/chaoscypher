# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 1 — bulk/reset methods on ChunkStorageProtocol.

Covers:
- count_chunks (total across all databases)
- count_staged_chunks (source_id IS NULL, scoped by database_name)
- clear_all_chunks (wholesale delete)
- delete_staged_chunks (staged only, scoped by database_name)

The staged chunks semantics match the legacy reset code in
``packages/cortex/shared/reset/data_reset.py`` which treats chunks with
NULL source_id as staged.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import DocumentChunk


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Full SqliteAdapter backed by a per-test tmp_path database."""
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed_source(adapter: SqliteAdapter, source_id: str, database_name: str = "test") -> None:
    """Seed a minimal valid source row."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )


def _seed(adapter: SqliteAdapter, source_ids: list[str | None]) -> None:
    """Seed one chunk per ``source_id``. None means staged (orphan) chunk."""
    created: set[str] = set()
    for sid in source_ids:
        if sid is not None and sid not in created:
            _seed_source(adapter, sid)
            created.add(sid)
    with adapter.transaction():
        for i, sid in enumerate(source_ids):
            adapter.session.add(
                DocumentChunk(
                    id=f"chunk-{i}",
                    database_name="test",
                    source_id=sid,  # type: ignore[arg-type]  # Column is nullable in SQL
                    chunk_index=i,
                    content=f"content {i}",
                )
            )


def test_count_chunks_all(adapter: SqliteAdapter) -> None:
    _seed(adapter, ["src-a", "src-a", None])
    assert adapter.count_chunks() == 3


def test_count_chunks_empty(adapter: SqliteAdapter) -> None:
    assert adapter.count_chunks() == 0


def test_count_staged_chunks(adapter: SqliteAdapter) -> None:
    _seed(adapter, ["src-a", None, None])
    assert adapter.count_staged_chunks(database_name="test") == 2


def test_count_staged_chunks_unscoped_untouched(adapter: SqliteAdapter) -> None:
    # Seed staged chunks in another database — should not count
    _seed(adapter, [None])
    with adapter.transaction():
        adapter.session.add(
            DocumentChunk(
                id="chunk-other",
                database_name="other",
                source_id=None,  # type: ignore[arg-type]
                chunk_index=0,
                content="x",
            )
        )
    assert adapter.count_staged_chunks(database_name="test") == 1
    assert adapter.count_staged_chunks(database_name="other") == 1


def test_clear_all_chunks_returns_count(adapter: SqliteAdapter) -> None:
    _seed(adapter, ["src-a", "src-a", None])
    assert adapter.clear_all_chunks() == 3
    assert adapter.count_chunks() == 0


def test_clear_all_chunks_empty_is_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_chunks() == 0


def test_delete_staged_chunks_leaves_assigned(adapter: SqliteAdapter) -> None:
    _seed(adapter, ["src-a", None, None])
    deleted = adapter.delete_staged_chunks(database_name="test")
    assert deleted == 2
    assert adapter.count_chunks() == 1
    assert adapter.count_staged_chunks(database_name="test") == 0
