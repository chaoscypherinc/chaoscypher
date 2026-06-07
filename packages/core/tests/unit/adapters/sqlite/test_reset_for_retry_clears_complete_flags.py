# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: reset_for_retry clears *_complete flags by new_status."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
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


def _seed_committed_source(adapter: SqliteAdapter, tmp_path: Path, source_id: str = "src1") -> None:
    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    # Force the row into ERROR with all complete flags set, simulating
    # a source that was committed and then aborted post-fact.
    adapter.update_file(
        source_id,
        "default",
        {
            "status": SourceStatus.ERROR,
            "indexing_complete": True,
            "extraction_complete": True,
            "commit_complete": True,
            "error_stage": "commit",
            "current_step": 5,
            "total_steps": 10,
            "step_description": "Committing entities 5/10",
            "current_extraction_job_id": "job_abc",
        },
    )


def test_reset_to_pending_clears_all_complete_flags(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_committed_source(adapter, tmp_path)
    adapter.reset_for_retry(
        source_id="src1", database_name="default", new_status=SourceStatus.PENDING
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["indexing_complete"] is False
    assert src["extraction_complete"] is False
    assert src["commit_complete"] is False
    assert src["current_extraction_job_id"] is None
    assert src["current_step"] == 0
    assert src["total_steps"] == 0
    assert src["step_description"] == ""


def test_reset_to_indexed_clears_extraction_and_commit_only(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    _seed_committed_source(adapter, tmp_path)
    adapter.reset_for_retry(
        source_id="src1", database_name="default", new_status=SourceStatus.INDEXED
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    # indexing stays valid — its chunks/embeddings are still on disk
    assert src["indexing_complete"] is True
    assert src["extraction_complete"] is False
    assert src["commit_complete"] is False
    assert src["current_extraction_job_id"] is None


def test_reset_to_extracted_clears_commit_only(adapter: SqliteAdapter, tmp_path: Path) -> None:
    _seed_committed_source(adapter, tmp_path)
    adapter.reset_for_retry(
        source_id="src1", database_name="default", new_status=SourceStatus.EXTRACTED
    )
    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["indexing_complete"] is True
    assert src["extraction_complete"] is True
    assert src["commit_complete"] is False
    assert src["current_extraction_job_id"] is None


# ---------------------------------------------------------------------------
# F44 — clear_commit_payload behavior
# ---------------------------------------------------------------------------


def _seed_with_payload(adapter: SqliteAdapter, tmp_path: Path, source_id: str = "srcp") -> None:
    """Seed an errored source carrying a non-empty commit_payload."""
    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    adapter.update_file(
        source_id,
        "default",
        {
            "status": SourceStatus.ERROR,
            "indexing_complete": True,
            "extraction_complete": True,
            "commit_complete": False,
            "error_stage": "commit",
        },
    )
    # Write a stale payload — would normally be set by queue_import_commit.
    adapter.set_source_commit_payload(
        source_id,
        {"entities": [{"name": "Stale"}], "relationships": []},
        database_name="default",
    )


def test_reset_for_retry_default_preserves_commit_payload(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """Default behavior: commit_payload is preserved (commit-only retry path)."""
    _seed_with_payload(adapter, tmp_path)
    adapter.reset_for_retry(
        source_id="srcp", database_name="default", new_status=SourceStatus.EXTRACTED
    )
    payload = adapter.get_source_commit_payload("srcp", database_name="default")
    assert payload is not None, "commit_payload must be preserved by default"
    assert payload["entities"] == [{"name": "Stale"}]


def test_reset_for_retry_clear_commit_payload_nulls_column(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """clear_commit_payload=True nulls the commit_payload column.

    F44: when retry transitions to a pre-commit stage (PENDING/INDEXED),
    the stale extraction payload must be discarded so the next commit
    can't pick up data ahead of the freshly-extracted payload.
    """
    _seed_with_payload(adapter, tmp_path)
    adapter.reset_for_retry(
        source_id="srcp",
        database_name="default",
        new_status=SourceStatus.INDEXED,
        clear_commit_payload=True,
    )
    payload = adapter.get_source_commit_payload("srcp", database_name="default")
    assert payload is None, "commit_payload must be NULL after clear=True"
