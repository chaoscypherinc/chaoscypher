# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the SourceRecovery module.

SourceRecovery is the source-level reconciler. It scans for
non-terminal SourceRow states and re-dispatches missing queue work
after a worker crash.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.services.sources.recovery import (
    RecoveryStats,
    SourceRecovery,
)


def test_recovery_stats_defaults() -> None:
    """RecoveryStats is a zero-initialized counter dataclass."""
    stats = RecoveryStats()
    assert stats.recovered == 0
    assert stats.skipped_paused == 0
    assert stats.skipped_healthy == 0
    assert stats.total_scanned == 0


def test_recovery_stats_to_dict() -> None:
    """to_dict is a flat serialization of all counters including skipped_exhausted."""
    stats = RecoveryStats(
        recovered=3,
        skipped_paused=2,
        skipped_healthy=5,
        skipped_exhausted=1,
        total_scanned=10,
    )
    assert stats.to_dict() == {
        "recovered": 3,
        "skipped_paused": 2,
        "skipped_healthy": 5,
        "skipped_exhausted": 1,
        "total_scanned": 10,
    }


@pytest.mark.asyncio
async def test_reconcile_database_empty_returns_zero_stats(
    in_memory_adapter,
) -> None:
    """A database with no non-terminal sources produces an all-zero stats object."""
    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=AsyncMock(),
    )
    stats = await recovery.reconcile_database(database_name="default")
    assert stats.total_scanned == 0
    assert stats.recovered == 0
    assert stats.skipped_paused == 0
    assert stats.skipped_healthy == 0


def _seed_non_terminal_source(
    adapter,
    *,
    source_id: str,
    status: str,
    auto_analyze: bool = True,
) -> None:
    """Helper: minimal source row seeded with the given status."""
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
            "auto_analyze": auto_analyze,
        }
    )


@pytest.mark.asyncio
async def test_pending_source_gets_index_document_dispatched(
    in_memory_adapter,
) -> None:
    """A source stuck in 'pending' after a crash gets INDEX_DOCUMENT re-enqueued.

    The reconciler detects the stale state and dispatches the task.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-new"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-1",
        status="pending",
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert stats.total_scanned == 1
    queue.enqueue.assert_awaited_once()
    call_kwargs = queue.enqueue.await_args.kwargs
    assert call_kwargs["operation"] == "index_document"
    assert call_kwargs["queue"] == "operations"
    # The handler expects file_id in the data payload, not source_id —
    # metadata carries source_id separately.
    assert call_kwargs["data"]["file_id"] == "src-1"
    assert call_kwargs["metadata"]["source_id"] == "src-1"


@pytest.mark.asyncio
async def test_indexing_source_with_no_queue_task_gets_redispatched(
    in_memory_adapter,
) -> None:
    """A source stuck in 'indexing' whose queue task is gone gets re-enqueued."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-new"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-2",
        status="indexing",
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    queue.enqueue.assert_awaited_once()
    assert queue.enqueue.await_args.kwargs["operation"] == "index_document"


@pytest.mark.asyncio
async def test_indexing_source_skipped_when_queue_task_still_present(
    in_memory_adapter,
) -> None:
    """If a queue task is still in flight for this source, the reconciler debounces.

    It does NOT re-dispatch.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=True)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-3",
        status="indexing",
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_indexed_source_with_auto_analyze_dispatches_analysis(
    in_memory_adapter,
) -> None:
    """A source stuck in 'indexed' whose auto_analyze flag is set gets re-enqueued.

    The indexing handler tried to queue import_analysis last time but
    the worker crashed before it landed.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-4",
        status="indexed",
        auto_analyze=True,
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    call_kwargs = queue.enqueue.await_args.kwargs
    assert call_kwargs["operation"] == "import_analysis"
    assert call_kwargs["data"]["file_id"] == "src-4"


@pytest.mark.asyncio
async def test_indexed_source_without_auto_analyze_is_healthy(
    in_memory_adapter,
) -> None:
    """An indexed source without auto-analyze is treated as healthy.

    The reconciler respects user control.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-5",
        status="indexed",
        auto_analyze=False,
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_extracted_source_dispatches_commit(
    in_memory_adapter,
) -> None:
    """A source stuck in 'extracted' gets import_commit re-enqueued.

    The source must carry non-empty ``extraction_results.entities`` —
    the empty-extraction guard added in ``d4eeb1ac`` skips dispatch
    otherwise to protect against erasing committed graph data on a
    re-dispatch with stale/missing payload.

    Since audit fix #C4 (reviewer revision), recovery routes through
    ``queue_utils.queue_import_commit`` so the task metadata carries both
    ``file_id`` and ``source_id`` — required by ``abort_processing``'s
    cancel-by-metadata path.
    """
    queue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-6",
        status="extracted",
    )
    # Stash a commit payload so the recovery service has data to
    # re-dispatch. The payload supplants the old extraction_results
    # JSON column (migration 0042) — set_source_commit_payload is the
    # canonical writer.
    in_memory_adapter.set_source_commit_payload(
        "src-6",
        {"entities": [{"name": "E1"}], "relationships": []},
        "default",
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    with patch(
        "chaoscypher_core.services.sources.recovery.queue_utils.queue_import_commit",
        new_callable=AsyncMock,
    ) as mock_qic:
        mock_qic.return_value = "task_t"
        stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    mock_qic.assert_awaited_once()
    _, kwargs = mock_qic.await_args
    assert kwargs["file_id"] == "src-6"
    assert kwargs["database_name"] == "default"


@pytest.mark.asyncio
async def test_extracting_with_no_job_redispatches_analysis(
    in_memory_adapter,
) -> None:
    """An 'extracting' source with no ChunkExtractionJob row re-enqueues import_analysis.

    The analysis handler crashed before creating the job, so the
    handler can start from scratch.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-7",
        status="extracting",
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    call_kwargs = queue.enqueue.await_args.kwargs
    assert call_kwargs["operation"] == "import_analysis"


@pytest.mark.asyncio
async def test_extracting_job_complete_dispatches_finalize(
    in_memory_adapter,
) -> None:
    """Extracting source whose job counters are already terminal enqueues finalize.

    Completed + failed >= total means chunks are done but the
    finalize handler never fired.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-8",
        status="extracting",
    )
    job_id = "job-complete"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-8",
        database_name="default",
    )
    # Set job status to running so get_active_extraction_job finds it
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 3})
    # Bump counters to completed=3, total=3 → terminal
    for _ in range(3):
        in_memory_adapter.increment_job_completed_and_check(
            job_id=job_id,
            database_name="default",
            outcome="completed",
        )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    call_kwargs = queue.enqueue.await_args.kwargs
    assert call_kwargs["operation"] == "finalize_extraction"
    assert call_kwargs["queue"] == "llm"


@pytest.mark.asyncio
async def test_committing_source_with_no_queue_task_redispatches_commit(
    in_memory_adapter,
) -> None:
    """A source stuck in 'committing' with no queue task gets import_commit re-enqueued.

    Safe because the Task 13 fast-path returns immediately when
    commit_complete is already set. Requires non-empty
    ``extraction_results.entities`` (see ``d4eeb1ac``) to avoid the
    "rebuild commit from empty payload" failure mode.

    Since audit fix #C4 (reviewer revision), recovery routes through
    ``queue_utils.queue_import_commit`` so the task metadata carries both
    ``file_id`` and ``source_id`` — required by ``abort_processing``'s
    cancel-by-metadata path.
    """
    queue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-10",
        status="committing",
    )
    in_memory_adapter.set_source_commit_payload(
        "src-10",
        {"entities": [{"name": "E1"}], "relationships": []},
        "default",
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    with patch(
        "chaoscypher_core.services.sources.recovery.queue_utils.queue_import_commit",
        new_callable=AsyncMock,
    ) as mock_qic:
        mock_qic.return_value = "task_t"
        stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    mock_qic.assert_awaited_once()
    _, kwargs = mock_qic.await_args
    assert kwargs["file_id"] == "src-10"
    assert kwargs["database_name"] == "default"


@pytest.mark.asyncio
async def test_extracting_job_incomplete_redispatches_pending_chunks(
    in_memory_adapter,
) -> None:
    """Extracting source with a job mid-flight re-dispatches pending chunks.

    Enqueue EXTRACT_CHUNK for each non-terminal task row via compound dispatch.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-9",
        status="extracting",
    )
    job_id = "job-incomplete"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-9",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 3})
    # 1 completed + 2 pending
    in_memory_adapter.create_chunk_task(
        task_id="task-0",
        job_id=job_id,
        database_name="default",
        chunk_index=0,
    )
    in_memory_adapter.update_chunk_task("task-0", {"status": "completed"})
    in_memory_adapter.create_chunk_task(
        task_id="task-1",
        job_id=job_id,
        database_name="default",
        chunk_index=1,
    )
    in_memory_adapter.create_chunk_task(
        task_id="task-2",
        job_id=job_id,
        database_name="default",
        chunk_index=2,
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    # Two pending tasks → two compound enqueues
    assert queue.enqueue.await_count == 2
    operations = [c.kwargs["operation"] for c in queue.enqueue.await_args_list]
    assert all(op == "extract_chunk" for op in operations)


@pytest.mark.asyncio
async def test_recover_source_redispatches_queued_chunks_after_paused_skip(
    in_memory_adapter,
) -> None:
    """Chunks left in 'queued' after a paused-skip are recovered on resume.

    Scenario: worker boots, dequeues a chunk, finds the source paused, returns
    {"skipped": "paused"}. The Valkey task acks but the chunk_extraction_tasks
    row stays at status='queued' (no handler updated it). User clicks resume.
    recover_source must re-enqueue the queued chunks — otherwise the import
    silently stalls forever even though the source is now unpaused.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-paused-skip",
        status="extracting",
    )
    job_id = "job-paused-skip"
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id="src-paused-skip",
        database_name="default",
    )
    in_memory_adapter.update_extraction_job(job_id, {"status": "running", "total_chunks": 3})
    # 1 completed, 2 stuck in 'queued' (the post-paused-skip state).
    in_memory_adapter.create_chunk_task(
        task_id="task-0", job_id=job_id, database_name="default", chunk_index=0
    )
    in_memory_adapter.update_chunk_task("task-0", {"status": "completed"})
    in_memory_adapter.create_chunk_task(
        task_id="task-1", job_id=job_id, database_name="default", chunk_index=1
    )
    in_memory_adapter.update_chunk_task("task-1", {"status": "queued"})
    in_memory_adapter.create_chunk_task(
        task_id="task-2", job_id=job_id, database_name="default", chunk_index=2
    )
    in_memory_adapter.update_chunk_task("task-2", {"status": "queued"})

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    # recover_source is the manual-resume entry point; bypasses stall debounce.
    recovered = await recovery.recover_source(source_id="src-paused-skip", database_name="default")

    assert recovered is True
    assert queue.enqueue.await_count == 2
    operations = [c.kwargs["operation"] for c in queue.enqueue.await_args_list]
    assert all(op == "extract_chunk" for op in operations)


# ====================================================================
# Heartbeat-based debounce — protects against the duplicate-dispatch
# race where a still-running handler is re-fired by the reconciler
# because its queue task is briefly invisible (claimed-between-states
# or just-completed). The reconciler now skips any source whose
# ``last_activity_at`` was bumped within the stall threshold.
# ====================================================================


@pytest.mark.asyncio
async def test_recently_active_source_is_skipped_by_bulk_reconciler(
    in_memory_adapter,
) -> None:
    """A source that heartbeated within the stall threshold is treated as healthy.

    Even if the queue scan finds no task for the source (the failure
    mode that produced 421 stale-chunk warnings on real imports), the
    reconciler must respect the heartbeat and not re-dispatch.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-fresh",
        status="indexing",
    )
    # Simulate a handler that heartbeated 5 seconds ago.
    in_memory_adapter.update_source_last_activity(
        source_id="src-fresh",
        database_name="default",
        at_time=datetime.now(UTC) - timedelta(seconds=5),
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=120,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()
    # The recently-active check short-circuits before the queue lookup.
    queue.task_exists_for_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_source_still_gets_redispatched(in_memory_adapter) -> None:
    """A source whose heartbeat is older than the threshold is re-dispatched.

    This is the genuine "handler crashed" case the reconciler exists
    to handle.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-stale"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-stale",
        status="indexing",
    )
    # Last heartbeat 10 minutes ago — well past the 120s threshold.
    in_memory_adapter.update_source_last_activity(
        source_id="src-stale",
        database_name="default",
        at_time=datetime.now(UTC) - timedelta(minutes=10),
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=120,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    queue.enqueue.assert_awaited_once()
    assert queue.enqueue.await_args.kwargs["operation"] == "index_document"


@pytest.mark.asyncio
async def test_source_with_no_last_activity_is_treated_as_stale(
    in_memory_adapter,
) -> None:
    """A source that never heartbeated (NULL last_activity_at) is recoverable.

    Initial uploads land in 'pending' before any handler touches them;
    the reconciler must still dispatch them.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-new"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-untouched",
        status="pending",
    )
    # Note: no update_source_last_activity call — last_activity_at is NULL.

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=120,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_recover_source_bypasses_stall_threshold(
    in_memory_adapter,
) -> None:
    """recover_source (manual resume) ignores the heartbeat debounce.

    When a user explicitly clicks "resume," the system honors that
    intent immediately even if a handler heartbeated seconds ago. The
    debounce only applies to automatic bulk reconciliation.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-manual"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_non_terminal_source(
        in_memory_adapter,
        source_id="src-manual",
        status="indexing",
    )
    in_memory_adapter.update_source_last_activity(
        source_id="src-manual",
        database_name="default",
        at_time=datetime.now(UTC),  # heartbeated this instant
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=120,
    )
    recovered = await recovery.recover_source(source_id="src-manual", database_name="default")

    assert recovered is True
    queue.enqueue.assert_awaited_once()


def test_stalled_threshold_defaults_to_class_constant() -> None:
    """Constructor defaults stalled_threshold_seconds when None passed."""
    recovery = SourceRecovery(adapter=AsyncMock(), queue_client=AsyncMock())
    assert recovery.stalled_threshold_seconds == SourceRecovery.DEFAULT_STALLED_THRESHOLD_SECONDS


def test_stalled_threshold_constructor_override() -> None:
    """Explicit stalled_threshold_seconds overrides the class default."""
    recovery = SourceRecovery(
        adapter=AsyncMock(),
        queue_client=AsyncMock(),
        stalled_threshold_seconds=42,
    )
    assert recovery.stalled_threshold_seconds == 42
