# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ChunkTasksLifecycleMixin.get_chunk_tasks_summary.

The method is a pure aggregate (status counts + entity/relationship sums)
answered by a single GROUP BY query — these tests pin the summary shape
for mixed statuses, non-zero sums, and the empty-job case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_job(adapter: SqliteAdapter, tmp_path: Path, job_id: str) -> str:
    """Create a source + extraction job to attach chunk tasks to."""
    source_id = f"src-{job_id}"
    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="t.txt",
        file_content=b"hello world",
        staging_dir=str(tmp_path),
    )
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
    return source_id


def _add_task(
    adapter: SqliteAdapter,
    *,
    task_id: str,
    job_id: str,
    chunk_index: int,
    status: str,
    entity_count: int = 0,
    relationship_count: int = 0,
) -> None:
    """Create one chunk task and drive it to the given status/counts."""
    adapter.create_chunk_task(
        task_id=task_id,
        job_id=job_id,
        database_name="default",
        chunk_index=chunk_index,
    )
    adapter.update_chunk_task(
        task_id,
        {
            "status": status,
            "entity_count": entity_count,
            "relationship_count": relationship_count,
        },
    )


def test_summary_mixed_statuses_and_sums(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> None:
    """Counts group by status; entity/relationship sums span all statuses."""
    job_id = "job-summary-1"
    _seed_job(sqlite_adapter, tmp_path, job_id)

    _add_task(
        sqlite_adapter,
        task_id="tsk-s1",
        job_id=job_id,
        chunk_index=0,
        status="completed",
        entity_count=5,
        relationship_count=3,
    )
    _add_task(
        sqlite_adapter,
        task_id="tsk-s2",
        job_id=job_id,
        chunk_index=1,
        status="completed",
        entity_count=2,
        relationship_count=1,
    )
    _add_task(
        sqlite_adapter,
        task_id="tsk-s3",
        job_id=job_id,
        chunk_index=2,
        status="failed",
    )
    _add_task(
        sqlite_adapter,
        task_id="tsk-s4",
        job_id=job_id,
        chunk_index=3,
        status="pending",
    )

    summary = sqlite_adapter.get_chunk_tasks_summary(job_id)

    assert summary["total"] == 4
    assert summary["by_status"] == {
        "pending": 1,
        "queued": 0,
        "running": 0,
        "completed": 2,
        "failed": 1,
    }
    assert summary["total_entities"] == 7
    assert summary["total_relationships"] == 4


def test_summary_empty_job(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> None:
    """A job with no chunk tasks yields all-zero counts and sums."""
    job_id = "job-summary-empty"
    _seed_job(sqlite_adapter, tmp_path, job_id)

    summary = sqlite_adapter.get_chunk_tasks_summary(job_id)

    assert summary["total"] == 0
    assert summary["by_status"] == {
        "pending": 0,
        "queued": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
    }
    assert summary["total_entities"] == 0
    assert summary["total_relationships"] == 0


def test_summary_scoped_to_job(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> None:
    """Tasks from another job never leak into the summary."""
    job_a = "job-summary-a"
    job_b = "job-summary-b"
    _seed_job(sqlite_adapter, tmp_path, job_a)
    _seed_job(sqlite_adapter, tmp_path, job_b)

    _add_task(
        sqlite_adapter,
        task_id="tsk-a1",
        job_id=job_a,
        chunk_index=0,
        status="completed",
        entity_count=9,
        relationship_count=9,
    )
    _add_task(
        sqlite_adapter,
        task_id="tsk-b1",
        job_id=job_b,
        chunk_index=0,
        status="failed",
    )

    summary = sqlite_adapter.get_chunk_tasks_summary(job_a)

    assert summary["total"] == 1
    assert summary["by_status"]["completed"] == 1
    assert summary["by_status"]["failed"] == 0
    assert summary["total_entities"] == 9
    assert summary["total_relationships"] == 9
