# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: reset_for_re_extraction returns a committed source to INDEXED state."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    yield adp
    adp.disconnect()


def test_reset_clears_extraction_and_commit_state(adapter: SqliteAdapter) -> None:
    """A committed source returns to INDEXED with all derived state cleared."""
    row = SourceRow(
        id="src_1",
        database_name="default",
        filename="doc.pdf",
        filepath="/tmp/doc.pdf",
        file_type="pdf",
        file_size=10,
        title="doc.pdf",
        source_type="pdf",
        status=SourceStatus.COMMITTED,
        extraction_complete=True,
        commit_complete=True,
        extraction_entities_count=1,
        extraction_relationships_count=0,
        extraction_started_at=datetime.now(UTC),
        extraction_completed_at=datetime.now(UTC),
        commit_started_at=datetime.now(UTC),
        commit_completed_at=datetime.now(UTC),
        commit_nodes_created=5,
        commit_edges_created=3,
        commit_templates_created=2,
        source_document_node_id="node_doc_1",
        recovery_attempts=4,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    adapter.session.add(row)
    adapter.session.commit()

    adapter.reset_for_re_extraction(source_id="src_1", database_name="default")
    refreshed = adapter.get_file("src_1", "default")
    assert refreshed is not None
    assert refreshed["status"] == SourceStatus.INDEXED
    assert refreshed["extraction_complete"] is False
    assert refreshed["commit_complete"] is False
    assert refreshed["extraction_entities_count"] == 0
    assert refreshed["extraction_relationships_count"] == 0
    assert refreshed["commit_nodes_created"] == 0
    assert refreshed["commit_edges_created"] == 0
    assert refreshed["commit_templates_created"] == 0
    assert refreshed["source_document_node_id"] is None
    assert refreshed["recovery_attempts"] == 0


def test_reset_is_no_op_on_unrelated_database(adapter: SqliteAdapter) -> None:
    """database_name scoping prevents accidental cross-database resets."""
    row = SourceRow(
        id="src_1",
        database_name="default",
        filename="doc.pdf",
        filepath="/tmp/doc.pdf",
        file_type="pdf",
        file_size=10,
        title="doc.pdf",
        source_type="pdf",
        status=SourceStatus.COMMITTED,
        commit_complete=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    adapter.session.add(row)
    adapter.session.commit()

    adapter.reset_for_re_extraction(source_id="src_1", database_name="other_db")
    refreshed = adapter.get_file("src_1", "default")
    assert refreshed is not None
    assert refreshed["status"] == SourceStatus.COMMITTED  # untouched


def test_reset_is_no_op_on_missing_source(adapter: SqliteAdapter) -> None:
    """Calling reset on a nonexistent source is silently a no-op."""
    adapter.reset_for_re_extraction(source_id="nonexistent", database_name="default")
    # Should not raise


def _insert_stage_progress(adapter: SqliteAdapter, source_id: str, stage_name: str) -> None:
    """Seed one llm_stage_progress row directly via SQL."""
    assert adapter.session is not None
    adapter.session.execute(
        text("""
        INSERT INTO llm_stage_progress (source_id, stage_name, total, processed, started_at, last_activity)
        VALUES (:sid, :stage, 10, 5, :now, :now)
        """),
        {"sid": source_id, "stage": stage_name, "now": datetime.now(UTC)},
    )
    adapter.session.commit()


def _count_stage_progress(adapter: SqliteAdapter, source_id: str) -> int:
    """Count llm_stage_progress rows for a given source."""
    assert adapter.session is not None
    result = adapter.session.execute(
        text("SELECT COUNT(*) FROM llm_stage_progress WHERE source_id = :sid"),
        {"sid": source_id},
    )
    return int(result.scalar_one())


def _seed_committed_source(adapter: SqliteAdapter, source_id: str) -> None:
    """Insert a minimal committed SourceRow."""
    assert adapter.session is not None
    row = SourceRow(
        id=source_id,
        database_name="default",
        filename="doc.pdf",
        filepath="/tmp/doc.pdf",
        file_type="pdf",
        file_size=10,
        title="doc.pdf",
        source_type="pdf",
        status=SourceStatus.COMMITTED,
        extraction_complete=True,
        commit_complete=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    adapter.session.add(row)
    adapter.session.commit()


def test_reset_clears_llm_stage_progress_for_source(adapter: SqliteAdapter) -> None:
    """reset_for_re_extraction deletes llm_stage_progress rows for the reset source."""
    _seed_committed_source(adapter, "src_a")
    _insert_stage_progress(adapter, "src_a", "mcp_extraction")
    _insert_stage_progress(adapter, "src_a", "vision")

    assert _count_stage_progress(adapter, "src_a") == 2

    adapter.reset_for_re_extraction(source_id="src_a", database_name="default")

    assert _count_stage_progress(adapter, "src_a") == 0


def test_reset_does_not_delete_other_sources_stage_progress(adapter: SqliteAdapter) -> None:
    """reset_for_re_extraction only removes llm_stage_progress rows for the named source."""
    _seed_committed_source(adapter, "src_target")
    _seed_committed_source(adapter, "src_bystander")
    _insert_stage_progress(adapter, "src_target", "mcp_extraction")
    _insert_stage_progress(adapter, "src_bystander", "mcp_extraction")

    adapter.reset_for_re_extraction(source_id="src_target", database_name="default")

    assert _count_stage_progress(adapter, "src_target") == 0
    assert _count_stage_progress(adapter, "src_bystander") == 1
