# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 4 — count + delete_all methods on SourceStorageProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


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


def _seed(adapter: SqliteAdapter, source_id: str, database_name: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}-{database_name}",
            "status": "indexed",
        }
    )


def test_count_sources_scoped_to_database(adapter: SqliteAdapter) -> None:
    _seed(adapter, "a", "test")
    _seed(adapter, "b", "test")
    _seed(adapter, "c", "other")
    assert adapter.count_sources(database_name="test") == 2
    assert adapter.count_sources(database_name="other") == 1


def test_count_sources_empty(adapter: SqliteAdapter) -> None:
    assert adapter.count_sources(database_name="test") == 0


def test_delete_all_sources_scoped_to_database(adapter: SqliteAdapter) -> None:
    _seed(adapter, "a", "test")
    _seed(adapter, "b", "test")
    _seed(adapter, "c", "other")
    deleted = adapter.delete_all_sources(database_name="test")
    assert deleted == 2
    assert adapter.count_sources(database_name="test") == 0
    assert adapter.count_sources(database_name="other") == 1


def test_delete_all_sources_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.delete_all_sources(database_name="test") == 0
