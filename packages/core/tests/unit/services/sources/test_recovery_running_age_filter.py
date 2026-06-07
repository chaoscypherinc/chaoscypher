# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recoverable 'running' chunks must be older than the stall threshold.

Regression test for the 2026-05-23 stuck-extracting bug. When the all-in-one
container rebuilds mid-extraction, the LLM worker dies while one or more
chunks are in ``status='running'`` (claimed but not yet terminal). The Valkey
in-flight queue entries are evicted at the same time, so:

  - The FINALIZE gate above (``non_terminal_tasks`` check) sees the running
    rows and refuses to dispatch finalize_extraction (it's waiting for them
    to settle).
  - The earlier ``pending/queued/failed``-only scan never sees them, so they
    are never re-dispatched.
  - The source stays in ``processing_status='extracting'`` forever, and
    because the import-service gate only allows one source to extract at a
    time, every other source's import_analysis hits ``extracting_count=1``
    and waits behind the zombie indefinitely.

The fix (recovery.py): add ``"running"`` to the recoverable-status scan and
apply an age filter on ``started_at`` (parallel to the existing ``queued_at``
filter), so:

  - Fresh running chunks (worker legitimately mid-call) are treated as
    in-flight and NOT re-dispatched.
  - Stale running chunks (worker died, Valkey lost the task) ARE
    re-dispatched.
  - Valkey's ``in_flight_chunk_task_ids`` continues to short-circuit the
    common case (chunk still has a live queue entry).
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
async def test_fresh_running_chunks_not_recovered(in_memory_adapter) -> None:
    """A running chunk with started_at < stall_threshold ago is treated as in-flight."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-fresh-running")
    job_id = "job-fresh-running"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-fresh-running",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 2})
    # Two chunks the worker just claimed (started_at = now).
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task(
        "task-1",
        {"status": "running", "started_at": datetime.now(UTC)},
    )
    in_memory_adapter.create_chunk_task(
        task_id="task-2", job_id=job_id, database_name="default", chunk_index=1
    )
    in_memory_adapter.update_chunk_task(
        "task-2",
        {"status": "running", "started_at": datetime.now(UTC)},
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0, "fresh running chunks must not trigger recovery"
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_running_chunks_recovered(in_memory_adapter) -> None:
    """A running chunk with started_at > stall_threshold ago is recovered as zombie."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # Worker died: Valkey has no in-flight task for the zombie.
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-stale-running")
    job_id = "job-stale-running"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-stale-running",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 1})
    # Worker claimed it 30 min ago and never reported back — clearly a zombie.
    long_ago = datetime.now(UTC) - timedelta(minutes=30)
    in_memory_adapter.create_chunk_task(
        task_id="task-zombie", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task(
        "task-zombie",
        {"status": "running", "started_at": long_ago},
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1, "stale running chunks must be re-dispatched"
    assert queue.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_running_zombie_with_completed_siblings_recovered(in_memory_adapter) -> None:
    """The bug-as-observed: 2 of 4 chunks completed, 2 left in 'running' after a rebuild.

    Without the fix the FINALIZE gate refuses (non-terminal tasks present) and the
    pending/queued/failed scan returns empty (running not in the list), so the source
    stalls indefinitely. With the fix the zombie running tasks get re-dispatched.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-rebuild-zombie")
    job_id = "job-rebuild-zombie"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-rebuild-zombie",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 4})
    long_ago = datetime.now(UTC) - timedelta(minutes=10)
    # 2 chunks completed before the rebuild.
    for i, tid in enumerate(("done-a", "done-b")):
        in_memory_adapter.create_chunk_task(
            task_id=tid, job_id=job_id, database_name="default", chunk_index=i
        )
        in_memory_adapter.update_chunk_task(tid, {"status": "completed", "completed_at": long_ago})
    # 2 chunks left running when the worker died.
    for i, tid in enumerate(("zombie-a", "zombie-b"), start=2):
        in_memory_adapter.create_chunk_task(
            task_id=tid, job_id=job_id, database_name="default", chunk_index=i
        )
        in_memory_adapter.update_chunk_task(tid, {"status": "running", "started_at": long_ago})

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1, "zombie source must be recovered"
    assert queue.enqueue.await_count == 2, "both zombie chunks must be re-dispatched"


@pytest.mark.asyncio
async def test_running_chunk_in_valkey_not_recovered(in_memory_adapter) -> None:
    """Even when stale by DB age, if Valkey still has the task it's NOT a zombie.

    The Valkey in-flight check is the primary signal; the age filter is a fallback
    for cases the Valkey check can't see (eviction, scan failure).
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # Worker is genuinely processing — Valkey still has the task.
    queue.in_flight_chunk_task_ids = AsyncMock(return_value={"task-live"})

    _seed_extracting_source(in_memory_adapter, source_id="src-live-running")
    job_id = "job-live-running"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-live-running",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 1})
    # Started 30 min ago — would be considered stale by the age filter alone,
    # but Valkey says the task is in-flight (long-running LLM call).
    long_ago = datetime.now(UTC) - timedelta(minutes=30)
    in_memory_adapter.create_chunk_task(
        task_id="task-live", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task(
        "task-live",
        {"status": "running", "started_at": long_ago},
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0, "Valkey-confirmed live task must NOT be recovered"
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_chunk_missing_started_at_recovered(in_memory_adapter) -> None:
    """A running chunk with no started_at falls back to recoverable (safety net)."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_chunk_task_ids = AsyncMock(return_value=set())

    _seed_extracting_source(in_memory_adapter, source_id="src-no-ts")
    job_id = "job-no-ts"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-no-ts",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 1})
    in_memory_adapter.create_chunk_task(
        task_id="task-no-ts", job_id=job_id, database_name="default", chunk_index=0
    )
    # Move to running but leave both started_at and queued_at null.
    in_memory_adapter.update_chunk_task("task-no-ts", {"status": "running"})

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=60,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1, "running chunk with no timestamp must default to recoverable"
    assert queue.enqueue.await_count == 1
