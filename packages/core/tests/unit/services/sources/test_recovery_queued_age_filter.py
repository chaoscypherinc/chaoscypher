# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recoverable 'queued' chunks must be older than the stall threshold.

Commit ``678728d7e`` added 'queued' to the pending-tasks scan to catch
the paused-skip orphan case (chunk handler returned {"skipped":
"paused"}, ack'd the Valkey task, but never moved the DB row off
'queued'). Without an age filter, that change also matches chunks that
are *legitimately* mid-flight — the worker just hasn't transitioned
the row to 'running' yet — and re-dispatches them, double-running the
work.

The Valkey-side filter (Slice 2,
``QueueClient.in_flight_chunk_task_ids``) catches the case where the
Valkey task still exists. This file pins the DB-side fallback: even if
Valkey says "no task," a chunk whose ``queued_at`` is fresher than
``stalled_threshold_seconds`` is treated as in-flight, not orphan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed_extracting_source(adapter, *, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
            "auto_analyze": True,
        }
    )


@pytest.mark.asyncio
async def test_fresh_queued_chunks_not_recovered(in_memory_adapter) -> None:
    """A queued chunk with queued_at < stall_threshold ago is treated as in-flight."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # Valkey reports no in-flight chunk tasks (e.g., post-paused-skip,
    # or scan failure). The age filter must still protect fresh chunks.
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-fresh")
    job_id = "job-fresh"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-fresh",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 2})
    # Two chunks both freshly queued (queued_at = now).
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task(
        "task-1",
        {"status": "queued", "queued_at": datetime.now(UTC)},
    )
    in_memory_adapter.create_chunk_task(
        task_id="task-2", job_id=job_id, database_name="default", chunk_index=1
    )
    in_memory_adapter.update_chunk_task(
        "task-2",
        {"status": "queued", "queued_at": datetime.now(UTC)},
    )

    # Stall threshold of 60s — both chunks queued <60s ago.
    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0, "fresh queued chunks must not trigger recovery"
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_queued_chunks_recovered(in_memory_adapter) -> None:
    """A queued chunk with queued_at > stall_threshold ago is recovered as orphan."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-stale")
    job_id = "job-stale"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-stale",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 1})
    # Queued 30 minutes ago — clearly orphan.
    long_ago = datetime.now(UTC) - timedelta(minutes=30)
    in_memory_adapter.create_chunk_task(
        task_id="task-old", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task(
        "task-old",
        {"status": "queued", "queued_at": long_ago},
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert queue.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_pending_chunks_not_age_filtered(in_memory_adapter) -> None:
    """The age filter applies ONLY to 'queued' chunks; pending/failed dispatch as-is."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-pending")
    job_id = "job-pending"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-pending",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 2})
    # Fresh pending chunk (no queued_at, no Valkey task) is recoverable.
    in_memory_adapter.create_chunk_task(
        task_id="task-pending",
        job_id=job_id,
        database_name="default",
        chunk_index=0,
    )
    # Status defaults to 'pending'.
    # Failed chunk also recoverable regardless of timing.
    in_memory_adapter.create_chunk_task(
        task_id="task-failed",
        job_id=job_id,
        database_name="default",
        chunk_index=1,
    )
    in_memory_adapter.update_chunk_task("task-failed", {"status": "failed"})

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert queue.enqueue.await_count == 2
