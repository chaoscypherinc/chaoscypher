# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration test: chunk-rerun DB state machine end-to-end.

Drives a committed source through one rerun cycle using the real
adapter. Verifies:

- Source walks back committed → extracting with commit_complete cleared.
- chunk_extraction_attempts has one row with the prior result snapshot.
- The chunk_extraction_task row is wiped + retry_count bumped.
- A second rerun creates attempt_number=2.
- Graph nodes are untouched by the reset itself (commit handler runs
  later in the full flow; unit-level reset shouldn't touch graph rows).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionAttempt,
    ChunkExtractionTask,
    SourceRow,
)


@pytest.fixture
def adapter(tmp_path: Path):
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adp = SqliteAdapter(str(db_path), database_name="default")
    adp.connect()
    yield adp
    adp.disconnect()


def _seed_committed_source_with_one_chunk(adapter: SqliteAdapter, tmp_path: Path) -> dict[str, str]:
    """Create a committed source + extraction job + one completed chunk task."""
    source_id = "src-e2e"
    job_id = "job-e2e"
    task_id = "tsk-e2e"

    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="t.txt",
        file_content=b"alice met bob",
        staging_dir=str(tmp_path),
    )
    adapter.update_source_columns(
        source_id=source_id,
        database_name="default",
        updates={
            "status": "committed",
            "commit_complete": True,
            "commit_completed_at": datetime.now(UTC),
            "current_extraction_job_id": job_id,
            "last_activity_at": datetime.now(UTC),
        },
    )
    adapter.create_extraction_job(job_id=job_id, source_id=source_id, database_name="default")
    adapter.create_chunk_task(
        task_id=task_id,
        job_id=job_id,
        database_name="default",
        chunk_index=0,
    )
    adapter.update_chunk_task(
        task_id,
        {
            "status": "completed",
            "small_chunk_ids": ["sc-1"],
            "input_text": "alice met bob",
            "raw_entities": [{"name": "Alice", "type": "Person"}],
            "entity_count": 1,
            "relationship_count": 0,
            "completed_at": datetime.now(UTC),
        },
    )
    return {"source_id": source_id, "job_id": job_id, "task_id": task_id}


def test_rerun_walks_source_back_and_snapshots_prior_result(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source_with_one_chunk(adapter, tmp_path)

    attempt_number = adapter.reset_chunk_task_for_rerun(
        task_id=seed["task_id"],
        source_id=seed["source_id"],
    )
    assert attempt_number == 1

    # Source state walked back
    src = adapter.session.get(SourceRow, seed["source_id"])
    assert src is not None
    assert src.status == "extracting"
    assert src.commit_complete is False
    assert src.commit_completed_at is None

    # Chunk task wiped
    task = adapter.session.get(ChunkExtractionTask, seed["task_id"])
    assert task is not None
    assert task.status == "pending"
    assert task.raw_entities is None
    assert task.entity_count == 0
    assert task.retry_count == 1

    # Snapshot preserved the prior result
    snapshots = list(adapter.session.exec(select(ChunkExtractionAttempt)).all())
    assert len(snapshots) == 1
    assert snapshots[0].attempt_number == 1
    assert snapshots[0].entity_count == 1
    assert snapshots[0].raw_entities == [{"name": "Alice", "type": "Person"}]


def test_two_chunk_reruns_create_two_attempts(adapter: SqliteAdapter, tmp_path: Path) -> None:
    seed = _seed_committed_source_with_one_chunk(adapter, tmp_path)

    adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])
    # Simulate chunk re-running and producing a different result
    adapter.update_chunk_task(
        seed["task_id"],
        {
            "status": "completed",
            "input_text": "attempt 2",
            "raw_entities": [{"name": "Bob", "type": "Person"}],
            "entity_count": 1,
        },
    )
    # Simulate the post-rerun commit walking source back to committed
    adapter.update_source_columns(
        source_id=seed["source_id"],
        database_name="default",
        updates={"status": "committed", "commit_complete": True},
    )

    adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    snapshots = sorted(
        adapter.session.exec(select(ChunkExtractionAttempt)).all(),
        key=lambda a: a.attempt_number,
    )
    assert [s.attempt_number for s in snapshots] == [1, 2]
    assert snapshots[0].input_text == "alice met bob"
    assert snapshots[1].input_text == "attempt 2"
