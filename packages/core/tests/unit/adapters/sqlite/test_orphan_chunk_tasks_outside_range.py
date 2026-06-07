# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Adapter-level tests for ``orphan_chunk_tasks_outside_range``.

The bulk-orphan helper underpins the re-analysis cleanup that prevents the
recovery loop seen on source fa992140-…: a prior analysis pass produced more
hierarchical groups than the current one, so task rows at chunk_index values
beyond the new ``total_chunks`` were left behind in non-terminal status and
thrashed SourceRecovery every 60s. The fix is to bulk-orphan those rows
in-band with the upsert.

These tests pin the SQL predicate semantics:
  - rows whose chunk_index is < threshold are never touched;
  - rows whose chunk_index is >= threshold AND status is non-terminal flip
    to ``orphaned`` with a reason on ``error_message``;
  - rows whose chunk_index is >= threshold but status is already terminal
    (completed, failed, cancelled, orphaned) are left alone — we never
    trample preserved work.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import ChunkExtractionTask


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    yield adp
    adp.disconnect()


def _seed_job_with_tasks(
    adapter: SqliteAdapter,
    *,
    tmp_path: Path,
    source_id: str,
    job_id: str,
    rows: list[tuple[int, str]] | None = None,
) -> None:
    """Insert a source + job + one chunk_extraction_task per (chunk_index, status)."""
    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename=f"{source_id}.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
    for idx, status in rows or []:
        adapter.create_chunk_task(
            task_id=f"{job_id}-task-{idx}",
            job_id=job_id,
            database_name="default",
            chunk_index=idx,
        )
        # create_chunk_task defaults to status='pending'; only update if different
        if status != "pending":
            adapter.update_chunk_task(
                f"{job_id}-task-{idx}",
                {
                    "status": status,
                    **({"completed_at": datetime.now(UTC)} if status == "completed" else {}),
                },
            )


def _statuses_by_index(adapter: SqliteAdapter, job_id: str) -> dict[int, str]:
    statement = select(ChunkExtractionTask.chunk_index, ChunkExtractionTask.status).where(
        ChunkExtractionTask.job_id == job_id
    )
    rows = adapter.session.execute(statement).all()
    return dict(rows)


def test_orphan_chunk_tasks_outside_range_marks_non_terminal_rows_orphaned(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """Non-terminal rows at chunk_index >= threshold transition to orphaned."""
    _seed_job_with_tasks(
        adapter,
        tmp_path=tmp_path,
        source_id="src-shrunk",
        job_id="job-shrunk",
        rows=[
            (0, "completed"),
            (1, "pending"),
            (2, "queued"),
            (3, "pending"),
            (4, "queued"),
            (5, "failed"),
        ],
    )

    rowcount = adapter.orphan_chunk_tasks_outside_range(
        job_id="job-shrunk",
        database_name="default",
        max_chunk_index=3,
    )

    # 21 phantoms-equivalent in this miniature: indices 3 and 4 are non-terminal
    # and >= threshold; 5 is failed (terminal) so it's preserved. 2 rows orphaned.
    assert rowcount == 2

    statuses = _statuses_by_index(adapter, "job-shrunk")
    assert statuses[0] == "completed"
    assert statuses[1] == "pending"
    assert statuses[2] == "queued"
    assert statuses[3] == "orphaned"
    assert statuses[4] == "orphaned"
    assert statuses[5] == "failed"  # terminal, preserved


def test_orphan_chunk_tasks_outside_range_preserves_completed_rows(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """A completed task at an out-of-range chunk_index keeps its results."""
    _seed_job_with_tasks(
        adapter,
        tmp_path=tmp_path,
        source_id="src-keep-work",
        job_id="job-keep-work",
        rows=[
            (0, "pending"),
            (1, "completed"),
            (2, "completed"),
            (3, "completed"),
        ],
    )

    rowcount = adapter.orphan_chunk_tasks_outside_range(
        job_id="job-keep-work",
        database_name="default",
        max_chunk_index=1,
    )

    # Indices 2 and 3 are out of range but completed — they must NOT be touched.
    assert rowcount == 0

    statuses = _statuses_by_index(adapter, "job-keep-work")
    assert statuses == {0: "pending", 1: "completed", 2: "completed", 3: "completed"}


def test_orphan_chunk_tasks_outside_range_noop_when_all_in_range(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """When no row's chunk_index is at or above the threshold, nothing changes."""
    _seed_job_with_tasks(
        adapter,
        tmp_path=tmp_path,
        source_id="src-stable",
        job_id="job-stable",
        rows=[(0, "pending"), (1, "pending"), (2, "queued")],
    )

    rowcount = adapter.orphan_chunk_tasks_outside_range(
        job_id="job-stable",
        database_name="default",
        max_chunk_index=3,
    )

    assert rowcount == 0
    statuses = _statuses_by_index(adapter, "job-stable")
    assert statuses == {0: "pending", 1: "pending", 2: "queued"}


def test_orphan_chunk_tasks_outside_range_scopes_to_job(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """The orphan call must not touch tasks belonging to a different job."""
    _seed_job_with_tasks(
        adapter,
        tmp_path=tmp_path,
        source_id="src-target",
        job_id="job-target",
        rows=[(0, "pending"), (5, "pending")],
    )
    _seed_job_with_tasks(
        adapter,
        tmp_path=tmp_path,
        source_id="src-other",
        job_id="job-other",
        rows=[(0, "pending"), (5, "pending")],
    )

    rowcount = adapter.orphan_chunk_tasks_outside_range(
        job_id="job-target",
        database_name="default",
        max_chunk_index=1,
    )

    assert rowcount == 1
    assert _statuses_by_index(adapter, "job-target") == {0: "pending", 5: "orphaned"}
    # job-other is untouched even though it has an identical layout.
    assert _statuses_by_index(adapter, "job-other") == {0: "pending", 5: "pending"}
