# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""recovery_attempts only increments when work is actually dispatched.

A compound recovery action where every chunk already has a live Valkey
queue task must be a true no-op: nothing enqueued, no counter
increment, no recovered count. Without this filter the reconciler
re-dispatches a source on every tick whenever the worker hasn't yet
claimed a chunk task — driving recovery_attempts toward the 10-attempt
exhaustion cap on healthy long-running sources.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed_non_terminal_source(
    adapter,
    *,
    source_id: str,
    status: str,
) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": status,
            "auto_analyze": True,
        }
    )


@pytest.mark.asyncio
async def test_compound_dispatch_skipped_when_all_chunks_in_flight(
    in_memory_adapter,
) -> None:
    """Every pending chunk has a live Valkey task → no dispatch, no counter bump.

    Pins the contract: ``in_flight_chunk_task_ids`` is consulted before
    building the compound action; if it covers every pending chunk, the
    classifier returns None and the source is treated as healthy.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # Every chunk task already has a queued/running Valkey entry.
    queue.in_flight_chunk_task_ids = AsyncMock(return_value={"task-1", "task-2"})

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-no-op",
        status="extracting",
    )
    job_id = "job-no-op"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-no-op",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 3})
    in_memory_adapter.create_chunk_task(
        task_id="task-0", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task("task-0", {"status": "completed"})
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=1
    )
    in_memory_adapter.create_chunk_task(
        task_id="task-2", job_id=job_id, database_name="default", chunk_index=2
    )

    before_attempts = in_memory_adapter.get_source("src-no-op", database_name="default").get(
        "recovery_attempts", 0
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0, "no-op recovery must not count as recovered"
    assert stats.skipped_healthy == 1, "fully-debounced source is healthy"
    queue.enqueue.assert_not_awaited()

    after_attempts = in_memory_adapter.get_source("src-no-op", database_name="default").get(
        "recovery_attempts", 0
    )
    assert after_attempts == before_attempts, (
        "recovery_attempts must NOT bump on no-op dispatch — got "
        f"{before_attempts} → {after_attempts}"
    )


@pytest.mark.asyncio
async def test_partial_in_flight_dispatches_only_orphaned_chunks(
    in_memory_adapter,
) -> None:
    """Three pending chunks, two in flight → dispatch only the third.

    Pins the contract: in_flight is a per-chunk filter, not a per-source
    short-circuit. Real work that needs dispatching still gets dispatched.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # task-1 and task-2 already have Valkey entries; task-3 is orphaned.
    queue.in_flight_chunk_task_ids = AsyncMock(return_value={"task-1", "task-2"})

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-partial",
        status="extracting",
    )
    job_id = "job-partial"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-partial",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 4})
    in_memory_adapter.create_chunk_task(
        task_id="task-0", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task("task-0", {"status": "completed"})
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=1
    )
    in_memory_adapter.create_chunk_task(
        task_id="task-2", job_id=job_id, database_name="default", chunk_index=2
    )
    in_memory_adapter.create_chunk_task(
        task_id="task-3", job_id=job_id, database_name="default", chunk_index=3
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert queue.enqueue.await_count == 1
    enqueued_data = queue.enqueue.await_args_list[0].kwargs.get("data") or {}
    assert enqueued_data.get("chunk_task_id") == "task-3"


@pytest.mark.asyncio
async def test_back_compat_when_queue_client_lacks_in_flight_method(
    in_memory_adapter,
) -> None:
    """A queue client without in_flight_chunk_task_ids degrades gracefully.

    The classifier proceeds with the existing behavior (dispatch all
    pending tasks). This keeps the contract loose so older / partial
    queue-client implementations don't break recovery.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # Deliberately omit in_flight_chunk_task_ids on the mock.
    if hasattr(queue, "in_flight_chunk_task_ids"):
        # AsyncMock auto-creates any accessed attribute, so we have to
        # explicitly remove it to simulate the back-compat case.
        del queue.in_flight_chunk_task_ids

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-bc",
        status="extracting",
    )
    job_id = "job-bc"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-bc",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 2})
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=1
    )
    in_memory_adapter.create_chunk_task(
        task_id="task-2", job_id=job_id, database_name="default", chunk_index=2
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    # Without the per-chunk filter, both chunks are dispatched (legacy behavior).
    assert stats.recovered == 1
    assert queue.enqueue.await_count == 2
