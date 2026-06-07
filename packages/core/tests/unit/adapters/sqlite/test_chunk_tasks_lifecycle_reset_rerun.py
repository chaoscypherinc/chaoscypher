# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ChunkTasksLifecycleMixin.reset_chunk_task_for_rerun.

Verifies the atomic snapshot + wipe + source-status walk-back used by
the per-chunk rerun feature (2026-05-15). Covers the happy path, race
guards, source-status idempotency, and clearing of commit_complete.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionAttempt,
    ChunkExtractionTask,
    SourceRow,
)
from chaoscypher_core.exceptions import ConflictError, NotFoundError


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_committed_source(adapter: SqliteAdapter, tmp_path: Path) -> dict[str, str]:
    """Create a committed source with a single completed chunk task."""
    source_id = "src-rerun-1"
    job_id = "job-rerun-1"
    task_id = "tsk-rerun-1"

    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="t.txt",
        file_content=b"hello world",
        staging_dir=str(tmp_path),
    )
    # Drive the source forward to committed
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
    adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name="default",
    )
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
            "input_text": "hello",
            "input_text_length": 5,
            "input_tokens": 10,
            "output_tokens": 20,
            "llm_response_json": '{"entities":[]}',
            "llm_response_length": 20,
            "llm_duration_ms": 1000,
            "raw_entities": [],
            "raw_relationships": [],
            "entity_count": 0,
            "relationship_count": 0,
            "filtering_log": {"version": 1, "total_removed": 0, "stages": []},
            "finish_reason": "stop",
            "completed_at": datetime.now(UTC),
            "retry_count": 0,
        },
    )
    return {"source_id": source_id, "job_id": job_id, "task_id": task_id}


def test_reset_creates_snapshot_with_attempt_number_1(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    attempt_number = sqlite_adapter.reset_chunk_task_for_rerun(
        task_id=seed["task_id"], source_id=seed["source_id"]
    )

    assert attempt_number == 1
    snapshot = sqlite_adapter.session.exec(
        select(ChunkExtractionAttempt).where(
            ChunkExtractionAttempt.chunk_task_id == seed["task_id"]
        )
    ).one()
    assert snapshot.attempt_number == 1
    assert snapshot.input_text == "hello"
    assert snapshot.llm_response_json == '{"entities":[]}'
    assert snapshot.entity_count == 0
    assert snapshot.finish_reason == "stop"


def test_reset_wipes_result_fields_and_bumps_retry_count(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    task = sqlite_adapter.session.get(ChunkExtractionTask, seed["task_id"])
    assert task is not None
    assert task.status == "pending"
    assert task.input_text is None
    assert task.llm_response_json is None
    assert task.raw_entities is None
    assert task.entity_count == 0
    assert task.filtering_log is None
    assert task.completed_at is None
    assert task.started_at is None
    assert task.retry_count == 1


def test_reset_walks_source_back_from_committed_to_extracting(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    source = sqlite_adapter.session.get(SourceRow, seed["source_id"])
    assert source is not None
    assert source.status == "extracting"
    assert source.commit_complete is False
    assert source.commit_completed_at is None


def test_reset_walks_source_from_extracted(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> None:
    """Source.status='extracted' (between finalize and commit) also walks back."""
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    sqlite_adapter.update_source_columns(
        source_id=seed["source_id"],
        database_name="default",
        updates={"status": "extracted", "commit_complete": False},
    )

    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    source = sqlite_adapter.session.get(SourceRow, seed["source_id"])
    assert source is not None
    assert source.status == "extracting"


def test_reset_attempt_number_increments_on_second_rerun(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    n1 = sqlite_adapter.reset_chunk_task_for_rerun(
        task_id=seed["task_id"], source_id=seed["source_id"]
    )
    # Simulate the chunk re-running and re-completing
    sqlite_adapter.update_chunk_task(
        seed["task_id"],
        {
            "status": "completed",
            "input_text": "second attempt",
            "llm_response_json": '{"entities":[{"name":"A"}]}',
            "entity_count": 1,
        },
    )

    n2 = sqlite_adapter.reset_chunk_task_for_rerun(
        task_id=seed["task_id"], source_id=seed["source_id"]
    )

    assert n1 == 1
    assert n2 == 2
    snapshots = list(
        sqlite_adapter.session.exec(
            select(ChunkExtractionAttempt)
            .where(ChunkExtractionAttempt.chunk_task_id == seed["task_id"])
            .order_by(ChunkExtractionAttempt.attempt_number)
        ).all()
    )
    assert [s.attempt_number for s in snapshots] == [1, 2]
    assert snapshots[1].input_text == "second attempt"
    assert snapshots[1].entity_count == 1


@pytest.mark.parametrize("blocked_status", ["pending", "queued", "running"])
def test_reset_blocked_for_non_terminal_status(
    sqlite_adapter: SqliteAdapter, tmp_path: Path, blocked_status: str
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    sqlite_adapter.update_chunk_task(seed["task_id"], {"status": blocked_status})

    with pytest.raises(ConflictError):
        sqlite_adapter.reset_chunk_task_for_rerun(
            task_id=seed["task_id"], source_id=seed["source_id"]
        )


def test_reset_raises_not_found_for_missing_task(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)

    with pytest.raises(NotFoundError):
        sqlite_adapter.reset_chunk_task_for_rerun(
            task_id="does-not-exist", source_id=seed["source_id"]
        )


def test_reset_updates_last_activity_at(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> None:
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    source_before = sqlite_adapter.session.get(SourceRow, seed["source_id"])
    assert source_before is not None
    original = source_before.last_activity_at

    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    # Expire the cached row so we re-read after the bulk UPDATE
    sqlite_adapter.session.expire_all()
    source_after = sqlite_adapter.session.get(SourceRow, seed["source_id"])
    assert source_after is not None
    assert original is not None
    assert source_after.last_activity_at is not None
    assert source_after.last_activity_at >= original
