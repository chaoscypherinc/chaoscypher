# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ChunkTasksLifecycleMixin.reset_chunk_task_for_rerun.

Verifies the atomic snapshot + wipe + source-status walk-back used by
the per-chunk rerun feature (2026-05-15). Covers the happy path, race
guards, source-status idempotency, and clearing of commit_complete.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionAttempt,
    ChunkExtractionTask,
    SourceRow,
)
from chaoscypher_core.exceptions import ConflictError, NotFoundError


if TYPE_CHECKING:
    from collections.abc import Sequence
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


def test_reset_loses_race_to_concurrent_rerun(
    sqlite_adapter: SqliteAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second caller's full reset lands between our status read and our UPDATE.

    Both callers pass the step-1 status pre-read; only the guarded UPDATE
    (compare-and-swap on the status) stops the second from snapshotting,
    wiping and enqueueing a duplicate paid extraction for the same chunk.
    """
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    rival = SqliteAdapter(str(sqlite_adapter.db_path), database_name="default")
    rival.connect()
    session = sqlite_adapter.session
    assert session is not None
    real_flush = session.flush
    raced = False

    def flush_after_rival_reset(objects: Sequence[Any] | None = None) -> None:
        nonlocal raced
        # Fire on the snapshot flush (step 3), i.e. after our step-1 read;
        # autoflushes before that read carry no pending attempt row.
        pending_snapshot = any(isinstance(obj, ChunkExtractionAttempt) for obj in session.new)
        if pending_snapshot and not raced:
            raced = True
            rival.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])
        real_flush(objects)

    monkeypatch.setattr(session, "flush", flush_after_rival_reset)
    try:
        with pytest.raises(ConflictError, match="lost race"):
            sqlite_adapter.reset_chunk_task_for_rerun(
                task_id=seed["task_id"], source_id=seed["source_id"]
            )
    finally:
        rival.disconnect()
    assert raced


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


def test_rerun_completion_reclaims_finalize(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> None:
    """After a finalized job, the rerun chunk's completion must claim finalize again.

    The first cycle spent the one-shot ``finalize_claimed`` claim and left the
    counters at total; without un-counting the prior outcome and releasing the
    claim, the rerun's increment is never terminal and no finalize is enqueued.
    """
    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    job_id = seed["job_id"]
    sqlite_adapter.update_extraction_job_total(
        job_id=job_id, total_chunks=1, database_name="default"
    )
    first = sqlite_adapter.increment_job_completed_and_check(
        job_id=job_id, database_name="default", outcome="completed"
    )
    assert first["is_terminal"] is True

    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    rerun = sqlite_adapter.increment_job_completed_and_check(
        job_id=job_id, database_name="default", outcome="completed"
    )
    assert rerun["is_terminal"] is True
    assert rerun["completed"] == 1


# --- The snapshot keeps every column of the try; the wipe resets every one (audit wave 6) ---

# Task columns a rerun deliberately does not copy into the history: the
# task's identity, its queue bookkeeping and the embeddings cache (large,
# re-derivable from raw_entities). A new task column fails the guard below
# until it is snapshotted (a column on ChunkExtractionAttempt) or listed here.
_TASK_COLUMNS_NOT_SNAPSHOTTED = frozenset(
    {
        "id",
        "job_id",
        "database_name",
        "chunk_index",
        "hierarchical_group_id",
        "small_chunk_ids",
        "queue_task_id",
        "status",
        "retry_count",
        "max_retries",
        "created_at",
        "queued_at",
        "cancelled_at",
        "raw_entity_embeddings",
    }
)


def test_every_task_column_is_snapshotted_or_deliberately_not() -> None:
    """The snapshot list is derived from the attempt row and covers every task column.

    Until wave 6 the snapshot and the wipe were two hand-written lists and
    ``decode_fallback`` (added to the task in 0010) was in neither.
    """
    from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_lifecycle import (
        ATTEMPT_SNAPSHOT_FIELDS,
    )

    task_columns = set(ChunkExtractionTask.model_fields)
    snapshotted = set(ATTEMPT_SNAPSHOT_FIELDS)
    assert snapshotted <= task_columns, sorted(snapshotted - task_columns)
    unclassified = task_columns - snapshotted - _TASK_COLUMNS_NOT_SNAPSHOTTED
    assert not unclassified, (
        "chunk task columns neither snapshotted into chunk_extraction_attempts "
        f"nor listed as deliberately not: {sorted(unclassified)}"
    )
    assert "decode_fallback" in snapshotted


def test_reset_keeps_decode_fallback_in_history_and_starts_the_rerun_pinned(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """A rung-2 try's fallback lands on the attempt row; the live task goes back to rung 0."""
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
        decode_fallback,
        ladder_step,
    )

    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    fallback = decode_fallback(2, seed=42, temperature=0.0)
    assert fallback is not None
    record = fallback.record()
    sqlite_adapter.update_chunk_task(seed["task_id"], {"decode_fallback": record})

    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    summaries = sqlite_adapter.list_chunk_attempts(chunk_task_id=seed["task_id"])
    assert [s["decode_fallback"] for s in summaries] == [record]
    detail = sqlite_adapter.get_chunk_attempt(summaries[0]["id"])
    assert detail is not None
    assert detail["decode_fallback"] == record
    assert detail["decode_fallback"]["repeat_penalty"] == fallback.repeat_penalty

    sqlite_adapter.session.expire_all()
    live = sqlite_adapter.get_chunk_task(seed["task_id"])
    assert live is not None
    assert live["decode_fallback"] is None
    assert ladder_step(live["decode_fallback"]) == 0


def test_reset_resets_every_snapshotted_column_to_its_default(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """Every column the history keeps is wiped on the task (None, or 0 for the counts)."""
    from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_lifecycle import (
        ATTEMPT_SNAPSHOT_FIELDS,
    )

    seed = _seed_committed_source(sqlite_adapter, tmp_path)
    sqlite_adapter.update_chunk_task(
        seed["task_id"],
        {
            "started_at": datetime.now(UTC),
            "context_window_available": 8192,
            "invalid_relationship_count": 2,
            "aborted_by_loop": True,
            "chunk_sentences": ["hello"],
            "error_message": "boom",
            "error_type": "llm_error",
            "decode_fallback": {"seed": 43, "temperature": 0.0, "step": 1},
            "raw_entity_embeddings": [[0.1]],
            "queued_at": datetime.now(UTC),
        },
    )

    sqlite_adapter.reset_chunk_task_for_rerun(task_id=seed["task_id"], source_id=seed["source_id"])

    sqlite_adapter.session.expire_all()
    task = sqlite_adapter.session.get(ChunkExtractionTask, seed["task_id"])
    assert task is not None
    for name in ATTEMPT_SNAPSHOT_FIELDS:
        expected = 0 if name.endswith("_count") else None
        assert getattr(task, name) == expected, name
    assert task.raw_entity_embeddings is None
    assert task.queued_at is None
