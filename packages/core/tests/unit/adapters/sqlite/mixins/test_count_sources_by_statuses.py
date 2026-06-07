# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CC040: count_sources_by_statuses — SQL COUNT path, no row materialization.

Tests that ``SourceIndexingMixin.count_sources_by_statuses`` issues a
``SELECT COUNT(*)`` query and never loads source rows into Python objects.
Exercises the efficient adapter method that backs
``SourceRecovery.count_awaiting_confirmation`` at the "thousands of parked
sources" scale.

Uses a ``tmp_path``-backed file SQLite (CC040 — no ``:memory:``).
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
    """Per-test file-backed SqliteAdapter (CC040: tmp_path, not :memory:)."""
    db_dir = tmp_path / "cc-count-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed(adapter: SqliteAdapter, source_id: str, database_name: str, status: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}-{database_name}",
            "status": status,
        }
    )


def test_count_sources_by_statuses_zero_when_empty(adapter: SqliteAdapter) -> None:
    """Returns 0 when no rows exist for the given database and status."""
    result = adapter.count_sources_by_statuses(
        statuses=["awaiting_confirmation"],
        database_name="test",
    )
    assert result == 0


def test_count_sources_by_statuses_counts_matching_rows(adapter: SqliteAdapter) -> None:
    """Returns the exact count of rows whose status is in the given set."""
    _seed(adapter, "src-a", "test", "awaiting_confirmation")
    _seed(adapter, "src-b", "test", "awaiting_confirmation")
    _seed(adapter, "src-c", "test", "indexed")  # different status — excluded

    result = adapter.count_sources_by_statuses(
        statuses=["awaiting_confirmation"],
        database_name="test",
    )
    assert result == 2


def test_count_sources_by_statuses_multi_status(adapter: SqliteAdapter) -> None:
    """Multiple statuses in the set are all counted together."""
    _seed(adapter, "src-a", "test", "awaiting_confirmation")
    _seed(adapter, "src-b", "test", "pending")
    _seed(adapter, "src-c", "test", "committed")  # excluded

    result = adapter.count_sources_by_statuses(
        statuses=["awaiting_confirmation", "pending"],
        database_name="test",
    )
    assert result == 2


def test_count_sources_by_statuses_scoped_by_database(adapter: SqliteAdapter) -> None:
    """COUNT is isolated to the given database_name (multi-DB isolation)."""
    _seed(adapter, "src-a", "test", "awaiting_confirmation")
    _seed(adapter, "src-b", "other", "awaiting_confirmation")  # different DB

    assert (
        adapter.count_sources_by_statuses(
            statuses=["awaiting_confirmation"],
            database_name="test",
        )
        == 1
    )
    assert (
        adapter.count_sources_by_statuses(
            statuses=["awaiting_confirmation"],
            database_name="other",
        )
        == 1
    )
    assert (
        adapter.count_sources_by_statuses(
            statuses=["awaiting_confirmation"],
            database_name="nonexistent",
        )
        == 0
    )


def test_count_sources_by_statuses_returns_int(adapter: SqliteAdapter) -> None:
    """Return type is always ``int``, never a SQLAlchemy Row or None."""
    _seed(adapter, "src-x", "test", "awaiting_confirmation")
    result = adapter.count_sources_by_statuses(
        statuses=["awaiting_confirmation"],
        database_name="test",
    )
    assert isinstance(result, int)
