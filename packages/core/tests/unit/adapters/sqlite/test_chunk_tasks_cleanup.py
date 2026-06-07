# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for adapter.cleanup_orphaned_chunk_tasks (Cluster F, F-2).

Verifies that cleanup_orphaned_chunk_tasks deletes rows with
status='orphaned' AND created_at older than the supplied cutoff, while
leaving all other rows untouched.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update as sqla_update
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import ChunkExtractionTask


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def in_memory_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Full SqliteAdapter backed by a per-test tmp_path database.

    Creates all tables via SQLModel.metadata.create_all() — the same path
    taken by initialize_database() — then connects and yields the adapter.
    Disconnects on teardown.
    """
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_COUNTER = 0
_TASK_COUNTER = 0


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    """Seed a minimal source row for FK satisfaction."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )


def _seed_job(adapter: SqliteAdapter, job_id: str, source_id: str) -> None:
    """Seed an extraction job row."""
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )


def _seed_task(
    adapter: SqliteAdapter,
    task_id: str,
    job_id: str,
    chunk_index: int,
) -> None:
    """Create a chunk task in pending state."""
    adapter.create_chunk_task(
        task_id=task_id,
        job_id=job_id,
        database_name="default",
        chunk_index=chunk_index,
    )


def _backdate_task(adapter: SqliteAdapter, task_id: str, created_at: datetime) -> None:
    """Force a task's created_at to the supplied timestamp via raw SQL update.

    Bypasses ORM model defaults so tests can place tasks at arbitrary ages
    without sleeping.
    """
    stmt = (
        sqla_update(ChunkExtractionTask)
        .where(ChunkExtractionTask.id == task_id)
        .values(created_at=created_at)
    )
    adapter.session.execute(stmt)
    adapter.session.commit()


def _set_status(adapter: SqliteAdapter, task_id: str, status: str) -> None:
    """Update a task's status field directly."""
    stmt = (
        sqla_update(ChunkExtractionTask)
        .where(ChunkExtractionTask.id == task_id)
        .values(status=status)
    )
    adapter.session.execute(stmt)
    adapter.session.commit()


def _task_exists(adapter: SqliteAdapter, task_id: str) -> bool:
    """Return True if the task row is still present in the DB."""
    return adapter.get_chunk_task(task_id) is not None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCleanupOldOrphanedTasks:
    """cleanup_orphaned_chunk_tasks deletes aged orphans and ignores fresh ones."""

    def test_cleanup_deletes_old_orphaned_tasks(self, in_memory_adapter: SqliteAdapter) -> None:
        """Orphaned tasks older than the cutoff are deleted; fresh ones remain.

        Arrange:
          - task-old-0, task-old-1: orphaned, created 10 days ago
          - task-fresh: orphaned, created just now
          - task-completed: completed, created 10 days ago (wrong status)
        Act: cleanup with 7-day retention (7 * 86400 seconds)
        Assert: 2 deleted; the fresh orphan and completed task still present.
        """
        source_id = "src-cleanup-1"
        job_id = "job-cleanup-1"
        _seed_source(in_memory_adapter, source_id)
        _seed_job(in_memory_adapter, job_id, source_id)

        ten_days_ago = datetime.now(UTC) - timedelta(days=10)

        # Two old orphaned tasks
        for i in range(2):
            tid = f"task-old-{i}"
            _seed_task(in_memory_adapter, tid, job_id, chunk_index=i)
            _set_status(in_memory_adapter, tid, "orphaned")
            _backdate_task(in_memory_adapter, tid, ten_days_ago)

        # One fresh orphaned task (created now — should survive)
        _seed_task(in_memory_adapter, "task-fresh", job_id, chunk_index=2)
        _set_status(in_memory_adapter, "task-fresh", "orphaned")

        # One old completed task (wrong status — should survive)
        _seed_task(in_memory_adapter, "task-completed", job_id, chunk_index=3)
        _set_status(in_memory_adapter, "task-completed", "completed")
        _backdate_task(in_memory_adapter, "task-completed", ten_days_ago)

        deleted = in_memory_adapter.cleanup_orphaned_chunk_tasks(older_than_seconds=7 * 86400)

        assert deleted == 2
        assert not _task_exists(in_memory_adapter, "task-old-0")
        assert not _task_exists(in_memory_adapter, "task-old-1")
        assert _task_exists(in_memory_adapter, "task-fresh")
        assert _task_exists(in_memory_adapter, "task-completed")

    def test_cleanup_returns_zero_when_no_matches(self, in_memory_adapter: SqliteAdapter) -> None:
        """Zero orphaned tasks yields zero deletes.

        All tasks are either fresh-orphaned (within retention window) or
        in non-orphaned statuses.
        """
        source_id = "src-cleanup-2"
        job_id = "job-cleanup-2"
        _seed_source(in_memory_adapter, source_id)
        _seed_job(in_memory_adapter, job_id, source_id)

        # Seed a few non-orphaned tasks
        for i, status in enumerate(("pending", "queued", "running", "completed", "failed")):
            tid = f"task-non-orphan-{i}"
            _seed_task(in_memory_adapter, tid, job_id, chunk_index=i)
            _set_status(in_memory_adapter, tid, status)

        deleted = in_memory_adapter.cleanup_orphaned_chunk_tasks(older_than_seconds=7 * 86400)

        assert deleted == 0

    def test_cleanup_preserves_non_orphaned_regardless_of_age(
        self, in_memory_adapter: SqliteAdapter
    ) -> None:
        """Non-orphaned tasks are never touched, even if very old.

        A 100-day-old completed task must survive cleanup.
        A 100-day-old failed task must survive cleanup.
        """
        source_id = "src-cleanup-3"
        job_id = "job-cleanup-3"
        _seed_source(in_memory_adapter, source_id)
        _seed_job(in_memory_adapter, job_id, source_id)

        ancient = datetime.now(UTC) - timedelta(days=100)

        for i, status in enumerate(("completed", "failed")):
            tid = f"task-ancient-{status}"
            _seed_task(in_memory_adapter, tid, job_id, chunk_index=i)
            _set_status(in_memory_adapter, tid, status)
            _backdate_task(in_memory_adapter, tid, ancient)

        deleted = in_memory_adapter.cleanup_orphaned_chunk_tasks(older_than_seconds=7 * 86400)

        assert deleted == 0
        assert _task_exists(in_memory_adapter, "task-ancient-completed")
        assert _task_exists(in_memory_adapter, "task-ancient-failed")

    def test_cleanup_respects_retention_cutoff_exactly(
        self, in_memory_adapter: SqliteAdapter
    ) -> None:
        """Tasks right at the boundary behave correctly.

        task-over-cutoff:  created_at = now - 7 days - 1 second  → deleted
        task-under-cutoff: created_at = now - 7 days + 1 second  → kept

        Both tasks are in 'orphaned' state.
        """
        source_id = "src-cleanup-4"
        job_id = "job-cleanup-4"
        _seed_source(in_memory_adapter, source_id)
        _seed_job(in_memory_adapter, job_id, source_id)

        retention_seconds = 7 * 86400
        now = datetime.now(UTC)
        just_over = now - timedelta(seconds=retention_seconds + 1)
        just_under = now - timedelta(seconds=retention_seconds - 1)

        _seed_task(in_memory_adapter, "task-over-cutoff", job_id, chunk_index=0)
        _set_status(in_memory_adapter, "task-over-cutoff", "orphaned")
        _backdate_task(in_memory_adapter, "task-over-cutoff", just_over)

        _seed_task(in_memory_adapter, "task-under-cutoff", job_id, chunk_index=1)
        _set_status(in_memory_adapter, "task-under-cutoff", "orphaned")
        _backdate_task(in_memory_adapter, "task-under-cutoff", just_under)

        deleted = in_memory_adapter.cleanup_orphaned_chunk_tasks(
            older_than_seconds=retention_seconds
        )

        assert deleted == 1
        assert not _task_exists(in_memory_adapter, "task-over-cutoff")
        assert _task_exists(in_memory_adapter, "task-under-cutoff")
