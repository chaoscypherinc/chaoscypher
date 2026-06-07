# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Audit fix #F53: reset_for_retry cancels the orphan ChunkExtractionJob.

When a source is reset for retry while it carries a
``current_extraction_job_id`` pointer, the underlying
``ChunkExtractionJob`` row plus any non-terminal
``ChunkExtractionTask`` rows must transition to ``cancelled``
in the same transaction. Otherwise the recovery reconciler — which
filters by job status, not by source pointer — can re-dispatch the
orphan chunks and cause duplicate extraction.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionJob,
    ChunkExtractionTask,
)
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


def _seed_errored_source_with_running_job(
    adapter: SqliteAdapter,
    tmp_path: Path,
    *,
    source_id: str = "src1",
    job_id: str = "job_orphan",
    completed_chunks: int = 3,
    inflight_chunks: int = 7,
) -> None:
    """Seed an errored source with a running job + tasks (3 done, 7 in flight)."""
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
            "extraction_complete": False,
            "commit_complete": False,
            "error_stage": "extraction",
            "current_extraction_job_id": job_id,
        },
    )
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
    adapter.update_extraction_job(
        job_id,
        {
            "status": "running",
            "total_chunks": completed_chunks + inflight_chunks,
            "completed_chunks": completed_chunks,
            "started_at": datetime.now(UTC),
        },
    )
    for i in range(completed_chunks):
        adapter.create_chunk_task(
            task_id=f"task_done_{i}",
            job_id=job_id,
            database_name="default",
            chunk_index=i,
        )
        adapter.update_chunk_task(
            f"task_done_{i}",
            {
                "status": "completed",
                "completed_at": datetime.now(UTC),
            },
        )
    inflight_states = ["pending", "queued", "running"]
    for i in range(inflight_chunks):
        adapter.create_chunk_task(
            task_id=f"task_inflight_{i}",
            job_id=job_id,
            database_name="default",
            chunk_index=completed_chunks + i,
        )
        adapter.update_chunk_task(
            f"task_inflight_{i}",
            {"status": inflight_states[i % len(inflight_states)]},
        )


def test_reset_for_retry_cancels_orphan_job_and_inflight_tasks(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """The full F53 scenario: 3 done / 7 in-flight becomes 3 done / 7 cancelled."""
    _seed_errored_source_with_running_job(adapter, tmp_path)

    adapter.reset_for_retry(
        source_id="src1",
        database_name="default",
        new_status=SourceStatus.INDEXED,
    )

    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["current_extraction_job_id"] is None
    assert src["status"] == SourceStatus.INDEXED

    adapter.session.expire_all()
    job = adapter.session.exec(
        select(ChunkExtractionJob).where(ChunkExtractionJob.id == "job_orphan")
    ).first()
    assert job is not None
    assert job.status == "cancelled"
    assert job.completed_at is not None

    tasks = adapter.session.exec(
        select(ChunkExtractionTask).where(ChunkExtractionTask.job_id == "job_orphan")
    ).all()
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1

    assert by_status.get("completed", 0) == 3, "completed tasks must NOT be touched"
    assert by_status.get("cancelled", 0) == 7, "all in-flight tasks must be cancelled"
    assert "pending" not in by_status
    assert "queued" not in by_status
    assert "running" not in by_status


def test_reset_for_retry_no_op_when_no_orphan_job(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """When the source has no current_extraction_job_id, nothing job-side happens."""
    adapter.upload_source(
        source_id="src2",
        database_name="default",
        filename="y.txt",
        file_content=b"y",
        staging_dir=str(tmp_path),
    )
    adapter.update_file(
        "src2",
        "default",
        {
            "status": SourceStatus.ERROR,
            "error_stage": "indexing",
            "current_extraction_job_id": None,
        },
    )

    adapter.reset_for_retry(
        source_id="src2",
        database_name="default",
        new_status=SourceStatus.PENDING,
    )

    src = adapter.get_source("src2", database_name="default")
    assert src is not None
    assert src["status"] == SourceStatus.PENDING


def test_reset_for_retry_idempotent_when_job_already_terminal(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """A second reset_for_retry call is a job-side no-op (already cancelled)."""
    _seed_errored_source_with_running_job(adapter, tmp_path, source_id="src3", job_id="job_t")

    adapter.reset_for_retry(
        source_id="src3", database_name="default", new_status=SourceStatus.INDEXED
    )
    adapter.update_file(
        "src3",
        "default",
        {
            "status": SourceStatus.ERROR,
            "error_stage": "extraction",
            "current_extraction_job_id": "job_t",
        },
    )

    adapter.reset_for_retry(
        source_id="src3", database_name="default", new_status=SourceStatus.INDEXED
    )

    adapter.session.expire_all()
    job = adapter.session.exec(
        select(ChunkExtractionJob).where(ChunkExtractionJob.id == "job_t")
    ).first()
    assert job is not None
    assert job.status == "cancelled"
