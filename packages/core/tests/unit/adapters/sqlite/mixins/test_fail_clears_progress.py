# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: fail_*/cancel_extraction clear stale progress and reset flags."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _seed(adapter: SqliteAdapter, status: str, **extras: Any) -> str:
    row = SourceRow(
        id="src_1",
        database_name="default",
        filename="doc.pdf",
        filepath="/tmp/doc.pdf",
        file_type="pdf",
        file_size=10,
        title="doc.pdf",
        source_type="pdf",
        status=status,
        current_step=2,
        total_steps=3,
        step_description="Analyzing chunk 1/5",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        **extras,
    )
    adapter.session.add(row)
    adapter.session.commit()
    return row.id


def test_fail_indexing_clears_progress(adapter: SqliteAdapter) -> None:
    src_id = _seed(adapter, SourceStatus.INDEXING)
    adapter.fail_indexing(src_id, "loader exploded")
    refreshed = adapter.get_file(src_id, "default")
    assert refreshed is not None
    assert refreshed["status"] == SourceStatus.ERROR
    assert refreshed["error_stage"] == "indexing"
    assert refreshed["error_message"] == "loader exploded"
    assert refreshed["current_step"] == 0
    assert refreshed["total_steps"] == 0
    assert refreshed["step_description"] == ""


def test_fail_extraction_clears_progress(adapter: SqliteAdapter) -> None:
    src_id = _seed(
        adapter,
        SourceStatus.EXTRACTING,
        current_extraction_job_id="job_abc",
    )
    adapter.fail_extraction(src_id, "llm timeout")
    refreshed = adapter.get_file(src_id, "default")
    assert refreshed is not None
    assert refreshed["status"] == SourceStatus.ERROR
    assert refreshed["error_stage"] == "extraction"
    assert refreshed["current_step"] == 0
    assert refreshed["total_steps"] == 0
    assert refreshed["current_extraction_job_id"] is None


def test_fail_commit_clears_progress(adapter: SqliteAdapter) -> None:
    """fail_commit's behavior must remain unchanged (it always cleared progress)."""
    src_id = _seed(adapter, SourceStatus.COMMITTING)
    adapter.fail_commit(src_id, "graph write failed")
    refreshed = adapter.get_file(src_id, "default")
    assert refreshed is not None
    assert refreshed["status"] == SourceStatus.ERROR
    assert refreshed["error_stage"] == "commit"
    assert refreshed["current_step"] == 0
    assert refreshed["step_description"] == ""


def test_cancel_extraction_resets_extraction_complete(adapter: SqliteAdapter) -> None:
    """Defensive: even if extraction_complete=True (theoretical race), cancel resets it."""
    src_id = _seed(
        adapter,
        SourceStatus.EXTRACTING,
        extraction_complete=True,
        current_extraction_job_id="job_abc",
    )
    adapter.cancel_extraction(src_id)
    refreshed = adapter.get_file(src_id, "default")
    assert refreshed is not None
    assert refreshed["status"] == SourceStatus.INDEXED
    assert refreshed["extraction_complete"] is False
    assert refreshed["current_extraction_job_id"] is None
    assert refreshed["current_step"] == 0


def test_fail_methods_no_op_on_missing_source(adapter: SqliteAdapter) -> None:
    """fail_* methods silently no-op if the source is missing (existing contract preserved)."""
    adapter.fail_indexing("nonexistent", "x")
    adapter.fail_extraction("nonexistent", "x")
    adapter.fail_commit("nonexistent", "x")
    adapter.cancel_extraction("nonexistent")
    # Should not raise
