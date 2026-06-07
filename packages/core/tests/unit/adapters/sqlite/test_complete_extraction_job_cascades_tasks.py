# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: complete_extraction_job orphans non-terminal child tasks."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.utils.id import generate_id


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def test_complete_extraction_job_orphans_running_tasks(adapter: SqliteAdapter) -> None:
    """Non-terminal tasks become orphaned when the parent job completes.

    Arrange: seed one source, one job, two chunk tasks (one completed, one
    running).
    Act:     call complete_extraction_job.
    Assert:  the running task transitions to orphaned with the expected
             error_message; the completed task remains unchanged.
    """
    source_id = generate_id(prefix="src")
    job_id = generate_id(prefix="job")
    task_done_id = generate_id(prefix="task")
    task_stuck_id = generate_id(prefix="task")

    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": "x.txt",
            "filepath": "/tmp/x.txt",
            "file_type": "text",
            "file_size": 10,
            "content_hash": generate_id(),
            "status": "extracting",
        }
    )
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
    adapter.update_extraction_job_total(job_id=job_id, total_chunks=2, database_name="default")

    adapter.create_chunk_task(
        task_id=task_done_id,
        job_id=job_id,
        database_name="default",
        chunk_index=0,
    )
    adapter.create_chunk_task(
        task_id=task_stuck_id,
        job_id=job_id,
        database_name="default",
        chunk_index=1,
    )

    # Force chunk 0 -> completed, chunk 1 -> running (the lingering one).
    adapter.update_chunk_task(task_done_id, {"status": "completed"})
    adapter.update_chunk_task(task_stuck_id, {"status": "running"})

    adapter.complete_extraction_job(job_id)

    stuck = adapter.get_chunk_task(task_stuck_id)
    assert stuck is not None
    assert stuck["status"] == "orphaned", (
        "non-terminal tasks must be orphaned when the parent job completes"
    )
    assert "parent job completed" in (stuck.get("error_message") or ""), (
        "orphaned task error_message must mention 'parent job completed'"
    )

    done = adapter.get_chunk_task(task_done_id)
    assert done is not None
    assert done["status"] == "completed", "terminal task must remain unchanged"
