# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``reset_to_indexed_for_re_extract`` cancels the running extraction job.

``POST /sources/{id}/re_extract`` on an EXTRACTING source used to NULL the
source's ``current_extraction_job_id`` pointer and nothing else. No handler
reads that pointer, so the "running handler discovers its slot has been
reassigned" checkpoint the comments promised did not exist: the old job's
chunk handlers kept running and writing, and the re-dispatched analysis
found the job still active (``get_active_extraction_job`` = pending/running)
and *resumed* it — so the user's re-extract silently reused the old results.

Cancelling the job row makes the chunk handler's EXISTING cancelled-status
guard the real checkpoint and takes the job out of the active-job lookup, so
the re-dispatch starts fresh.
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
    """Real SqliteAdapter over a tmp_path SQLite file."""
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    yield adp
    adp.disconnect()


def _seed_extracting_source_with_running_job(
    adapter: SqliteAdapter,
    tmp_path: Path,
    *,
    source_id: str = "src1",
    job_id: str = "job_running",
    completed_chunks: int = 2,
    inflight_chunks: int = 6,
) -> None:
    """Seed an EXTRACTING source with a running job + tasks (2 done, 6 in flight)."""
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
    adapter.update_extraction_job(
        job_id,
        {
            "status": "running",
            "total_chunks": completed_chunks + inflight_chunks,
            "completed_chunks": completed_chunks,
            "started_at": datetime.now(UTC),
        },
    )
    # create_extraction_job already set the pointer; the status is what the
    # re-extract endpoint sees when the user hits the button mid-extraction.
    adapter.update_file(
        source_id,
        "default",
        {
            "status": SourceStatus.EXTRACTING,
            "indexing_complete": True,
            "extraction_complete": False,
            "commit_complete": False,
        },
    )
    for i in range(completed_chunks):
        adapter.create_chunk_task(
            task_id=f"{job_id}_done_{i}",
            job_id=job_id,
            database_name="default",
            chunk_index=i,
        )
        adapter.update_chunk_task(
            f"{job_id}_done_{i}",
            {"status": "completed", "completed_at": datetime.now(UTC)},
        )
    inflight_states = ["pending", "queued", "running"]
    for i in range(inflight_chunks):
        adapter.create_chunk_task(
            task_id=f"{job_id}_inflight_{i}",
            job_id=job_id,
            database_name="default",
            chunk_index=completed_chunks + i,
        )
        adapter.update_chunk_task(
            f"{job_id}_inflight_{i}",
            {"status": inflight_states[i % len(inflight_states)]},
        )


def _job(adapter: SqliteAdapter, job_id: str) -> ChunkExtractionJob:
    """Re-read a job row straight from the DB, bypassing session cache."""
    adapter.session.expire_all()
    job = adapter.session.exec(
        select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
    ).first()
    assert job is not None
    return job


def test_re_extract_reset_cancels_the_running_job(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """The job row transitions to cancelled — the checkpoint handlers actually read."""
    _seed_extracting_source_with_running_job(adapter, tmp_path)

    adapter.reset_to_indexed_for_re_extract("src1")

    src = adapter.get_source("src1", database_name="default")
    assert src is not None
    assert src["status"] == SourceStatus.INDEXED
    assert src["current_extraction_job_id"] is None

    job = _job(adapter, "job_running")
    assert job.status == "cancelled"
    assert job.completed_at is not None


def test_re_extract_reset_cancels_inflight_tasks_but_not_completed_ones(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """2 done / 6 in flight becomes 2 done / 6 cancelled — no work is re-attributed."""
    _seed_extracting_source_with_running_job(adapter, tmp_path)

    adapter.reset_to_indexed_for_re_extract("src1")

    adapter.session.expire_all()
    tasks = adapter.session.exec(
        select(ChunkExtractionTask).where(ChunkExtractionTask.job_id == "job_running")
    ).all()
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1

    assert by_status.get("completed", 0) == 2, "completed tasks must NOT be touched"
    assert by_status.get("cancelled", 0) == 6, "all in-flight tasks must be cancelled"
    assert "pending" not in by_status
    assert "queued" not in by_status
    assert "running" not in by_status


def test_re_extract_reset_makes_the_re_dispatch_start_fresh(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """After the reset the analysis handler sees no active job, so it creates one.

    ``_import_analysis_handler`` resumes when ``get_active_extraction_job``
    returns a row and creates a fresh job when it returns None (pinned in
    ``tests/unit/services/sources/test_analysis_restart.py``). This is the
    adapter-side half of that contract: the reset must flip the answer.
    """
    _seed_extracting_source_with_running_job(adapter, tmp_path)

    before = adapter.get_active_extraction_job(source_id="src1", database_name="default")
    assert before is not None, "precondition: the old job is active and would be resumed"
    assert before["id"] == "job_running"

    adapter.reset_to_indexed_for_re_extract("src1")

    after = adapter.get_active_extraction_job(source_id="src1", database_name="default")
    assert after is None, "a cancelled job must not be resumable by the re-dispatch"


def test_re_extract_reset_leaves_other_sources_jobs_alone(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """Cancellation is scoped to the reset source — a neighbour keeps extracting."""
    _seed_extracting_source_with_running_job(adapter, tmp_path)
    _seed_extracting_source_with_running_job(
        adapter, tmp_path, source_id="src2", job_id="job_other"
    )

    adapter.reset_to_indexed_for_re_extract("src1")

    assert _job(adapter, "job_running").status == "cancelled"
    assert _job(adapter, "job_other").status == "running"

    other = adapter.get_active_extraction_job(source_id="src2", database_name="default")
    assert other is not None
    assert other["id"] == "job_other"


def test_re_extract_reset_does_not_rewrite_a_job_that_just_completed(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """A job that finished before the reset lands keeps its terminal status.

    The active-status SELECT is what protects it: a completed job is never
    in the cancel list, so "extraction finished, then the user pressed
    re-extract" cannot rewrite history into a cancelled-after-complete row.
    (A completion committing *inside* the reset transaction is not visible
    to it and loses to the cancel — deliberate: the user asked for that
    run's results to be discarded.)
    """
    _seed_extracting_source_with_running_job(adapter, tmp_path)
    adapter.complete_extraction_job("job_running")
    completed_at = _job(adapter, "job_running").completed_at

    adapter.reset_to_indexed_for_re_extract("src1")

    job = _job(adapter, "job_running")
    assert job.status == "completed"
    assert job.completed_at == completed_at


def test_re_extract_reset_cancels_every_active_job_for_the_source(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """Two active jobs (pointer only names the newest) both stop being resumable.

    ``current_extraction_job_id`` is a single slot, so a stale pending job
    left behind by an earlier pass is invisible to the pointer — but
    ``get_active_extraction_job`` would still hand it to the re-dispatch.
    Cancellation therefore follows the same active-status predicate.
    """
    _seed_extracting_source_with_running_job(adapter, tmp_path)
    adapter.create_extraction_job(
        job_id="job_second",
        source_id="src1",
        database_name="default",
    )

    adapter.reset_to_indexed_for_re_extract("src1")

    assert _job(adapter, "job_running").status == "cancelled"
    assert _job(adapter, "job_second").status == "cancelled"
    assert adapter.get_active_extraction_job(source_id="src1", database_name="default") is None


def test_a_queued_finalize_cannot_restart_the_cancelled_job(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """``start_extraction_job`` refuses a terminal job, so the cancel sticks.

    Without the refusal a finalize task queued just before the re-extract
    would flip cancelled → running, putting the abandoned job back in
    ``get_active_extraction_job``'s answer for the fresh analysis to resume.
    """
    _seed_extracting_source_with_running_job(adapter, tmp_path)
    adapter.reset_to_indexed_for_re_extract("src1")

    assert adapter.start_extraction_job("job_running") is False

    assert _job(adapter, "job_running").status == "cancelled"
    assert adapter.get_active_extraction_job(source_id="src1", database_name="default") is None


def test_start_extraction_job_still_starts_a_pending_job(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """The refusal is scoped to terminal jobs — the normal start still works."""
    _seed_extracting_source_with_running_job(
        adapter, tmp_path, source_id="src_fresh", job_id="job_fresh"
    )
    adapter.update_extraction_job("job_fresh", {"status": "pending", "started_at": None})

    assert adapter.start_extraction_job("job_fresh") is True

    job = _job(adapter, "job_fresh")
    assert job.status == "running"
    assert job.started_at is not None


def test_re_extract_reset_is_a_no_op_without_any_job(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """An INDEXED source that never started extracting resets without error."""
    adapter.upload_source(
        source_id="src_plain",
        database_name="default",
        filename="plain.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )

    adapter.reset_to_indexed_for_re_extract("src_plain")

    src = adapter.get_source("src_plain", database_name="default")
    assert src is not None
    assert src["status"] == SourceStatus.INDEXED
    assert src["current_extraction_job_id"] is None
