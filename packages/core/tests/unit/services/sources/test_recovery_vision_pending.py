# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the vision_pending recovery branch.

Source-recovery scanner re-enqueues OP_VISION_PAGE for stalled
``vision_pending`` sources. Two sub-cases:

1. Job mid-flight (PENDING pages remain) — compound dispatch OP_VISION_PAGE
   for each stalled PENDING row.
2. Job terminal (completed + failed >= total_pages) — dispatch
   OP_VISION_FINALIZE on QUEUE_OPERATIONS.

Guard cases:
- source with no vision_job row → skip (healthy).
- source recently active → skip (heartbeat debounce).
- source with pending rows but queue task already in flight → debounce.

The mid-flight debounce is PAGE-scoped (``QueueClient.in_flight_vision_page_ids``),
not source-scoped: vision pages are enqueued as one N-page batch and drained
one at a time (QUEUE_LLM concurrency=1), so a coarse "any task live for this
source" check would suppress recovery of a genuinely lost page for as long as
ANY sibling page in the batch is still live. Mirrors ``in_flight_chunk_task_ids``,
the filter ``_classify_extracting`` uses for its own compound (per-chunk)
dispatch for the same reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_vision_pending_source(
    adapter,
    *,
    source_id: str,
) -> None:
    """Insert a minimal source row in status=vision_pending."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 1024,
            "content_hash": f"hash-{source_id}",
            "status": "vision_pending",
        }
    )


def _create_vision_job_and_pages(
    adapter,
    *,
    source_id: str,
    page_count: int,
    completed: int = 0,
    failed: int = 0,
) -> str:
    """Create a vision_job with ``page_count`` PENDING pages.

    After creation, bumps ``completed`` / ``failed`` counters directly
    to simulate pages that already finished processing.

    Returns the vision_job id.
    """
    pages = [
        {
            "page_number": i + 1,
            "kind": VisionPageKind.PDF_PAGE,
            "image_path": f"/tmp/{source_id}_p{i + 1}.png",
        }
        for i in range(page_count)
    ]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    # Bump completed/failed counters to simulate prior processing.
    page_rows = adapter.list_vision_page_descriptions(source_id)
    for i in range(completed):
        adapter.increment_vision_job_completed_and_check(
            job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
        )
        # Mark the corresponding page row SUCCEEDED so it won't show
        # as PENDING in the recovery list query.
        adapter.update_vision_page_description(
            page_id=page_rows[i]["id"],
            new_status=VisionPageStatus.SUCCEEDED,
            description="ok",
            finish_reason="stop",
            error_message=None,
        )
    for i in range(failed):
        adapter.increment_vision_job_completed_and_check(
            job_id=job_id, outcome=VisionPageStatus.FAILED
        )
        idx = completed + i
        adapter.update_vision_page_description(
            page_id=page_rows[idx]["id"],
            new_status=VisionPageStatus.FAILED,
            description=None,
            finish_reason=None,
            error_message="llm error",
        )

    return job_id


# ---------------------------------------------------------------------------
# Sub-case 1: mid-flight job — compound OP_VISION_PAGE dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vision_pending_source_re_enqueues_pending_pages(
    in_memory_adapter,
) -> None:
    """A stalled vision_pending source with PENDING pages gets OP_VISION_PAGE re-enqueued.

    3 pages created, 1 already SUCCEEDED → 2 PENDING remain.
    The reconciler must compound-dispatch OP_VISION_PAGE for the 2 pending rows.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-vp"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    # No live tasks -> the page-scoped filter passes every PENDING row
    # through unchanged. Set explicitly rather than relying on AsyncMock's
    # auto-attribute default (a bare AsyncMock() would still evaluate
    # truthy/degrade in a way that happens to dispatch everything here, but
    # that's an accident of the mock library, not an assertion this test
    # should depend on).
    queue.in_flight_vision_page_ids = AsyncMock(return_value=set())

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-1")
    _create_vision_job_and_pages(
        in_memory_adapter,
        source_id="src-vp-1",
        page_count=3,
        completed=1,  # 1 SUCCEEDED, 2 still PENDING
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert stats.total_scanned == 1
    # Two PENDING pages → two compound enqueues
    assert queue.enqueue.await_count == 2
    operations = [c.kwargs["operation"] for c in queue.enqueue.await_args_list]
    assert all(op == "vision_page" for op in operations)
    queues = [c.kwargs["queue"] for c in queue.enqueue.await_args_list]
    assert all(q == "llm" for q in queues)
    # Each enqueue carries page_id, job_id, source_id in data
    for call in queue.enqueue.await_args_list:
        data = call.kwargs["data"]
        assert "page_id" in data
        assert data["source_id"] == "src-vp-1"
        assert "job_id" in data


@pytest.mark.asyncio
async def test_vision_pending_all_pages_pending_gets_all_re_enqueued(
    in_memory_adapter,
) -> None:
    """All 4 pages PENDING → all 4 OP_VISION_PAGE tasks are re-enqueued."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_vision_page_ids = AsyncMock(return_value=set())

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-allpending")
    _create_vision_job_and_pages(
        in_memory_adapter,
        source_id="src-vp-allpending",
        page_count=4,
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert queue.enqueue.await_count == 4
    operations = [c.kwargs["operation"] for c in queue.enqueue.await_args_list]
    assert all(op == "vision_page" for op in operations)


# ---------------------------------------------------------------------------
# Sub-case 2: terminal job — dispatch OP_VISION_FINALIZE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vision_pending_terminal_job_dispatches_finalize(
    in_memory_adapter,
) -> None:
    """A vision_pending source whose job is terminal (all pages done) gets
    OP_VISION_FINALIZE enqueued on QUEUE_OPERATIONS.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-vf"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-terminal")
    _create_vision_job_and_pages(
        in_memory_adapter,
        source_id="src-vp-terminal",
        page_count=3,
        completed=2,
        failed=1,  # 2+1=3 >= total_pages=3 → terminal
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    queue.enqueue.assert_awaited_once()
    call_kwargs = queue.enqueue.await_args.kwargs
    assert call_kwargs["operation"] == "vision_finalize"
    assert call_kwargs["queue"] == "operations"
    assert call_kwargs["data"]["source_id"] == "src-vp-terminal"
    assert "job_id" in call_kwargs["data"]
    assert call_kwargs["data"]["database_name"] == "default"


# ---------------------------------------------------------------------------
# Guard cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vision_pending_no_job_row_is_skipped(
    in_memory_adapter,
) -> None:
    """A vision_pending source with no vision_job row is skipped.

    The indexing handler crashed before creating the job — nothing to
    re-enqueue at the vision layer. The source will be recovered when
    its state transitions back through the pending/indexing branch.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-nojob")
    # Deliberately do NOT create a vision job.

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    # No dispatch (no job to recover) but still counted as recovered
    # because the classify branch returned None → skipped_healthy.
    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_pending_recently_active_is_debounced(
    in_memory_adapter,
) -> None:
    """A vision_pending source that heartbeated recently is not re-dispatched.

    The stall-threshold debounce applies to vision_pending the same way
    it applies to every other non-terminal status.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-fresh")
    _create_vision_job_and_pages(in_memory_adapter, source_id="src-vp-fresh", page_count=2)
    # Simulate a handler heartbeat 10 seconds ago (well within the 600s threshold).
    in_memory_adapter.update_source_last_activity(
        source_id="src-vp-fresh",
        database_name="default",
        at_time=datetime.now(UTC) - timedelta(seconds=10),
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=600,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_pending_queue_task_in_flight_is_debounced(
    in_memory_adapter,
) -> None:
    """If a vision_page queue task is already live for this source, skip.

    The ``task_exists_for_source`` debounce prevents double-dispatch
    for the terminal-job finalize path.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    # Queue reports a task is already live for vision_finalize.
    queue.task_exists_for_source = AsyncMock(return_value=True)

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-inflight")
    _create_vision_job_and_pages(
        in_memory_adapter,
        source_id="src-vp-inflight",
        page_count=2,
        completed=2,  # terminal
    )

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_pending_mid_flight_only_dispatches_missing_pages(
    in_memory_adapter,
) -> None:
    """A MIXED live/lost wave dispatches ONLY the genuinely missing pages.

    Regression test for entry 920 (fix-round follow-up): the mid-flight
    sub-branch previously had NO debounce at all, then briefly had a
    source-level ``_queue_has_task_for`` debounce that fixed the billable-
    duplicate bug but introduced a coarser regression of its own — on a
    5-page job where 1 page is still live and 2 were lost (worker crash /
    Valkey eviction), a source-level "any task live" check would suppress
    the WHOLE compound dispatch until the live page drains, which at
    QUEUE_LLM concurrency=1 can be most of the job's duration. The fix is
    page-scoped, mirroring ``in_flight_chunk_task_ids``
    (``_classify_extracting``'s per-chunk filter): only pages NOT in the
    in-flight set get re-dispatched.

    3 PENDING pages; page 1 is reported live, pages 2-3 are not -> exactly
    2 enqueues, for pages 2 and 3 only.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-mixed")
    _create_vision_job_and_pages(
        in_memory_adapter,
        source_id="src-vp-mixed",
        page_count=3,
    )
    pending_rows = in_memory_adapter.list_vision_page_descriptions("src-vp-mixed")
    live_page_id = pending_rows[0]["id"]
    lost_page_ids = {row["id"] for row in pending_rows[1:]}

    # Only page 1's task is still live on the queue.
    queue.in_flight_vision_page_ids = AsyncMock(return_value={live_page_id})

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert queue.enqueue.await_count == 2
    dispatched_page_ids = {c.kwargs["data"]["page_id"] for c in queue.enqueue.await_args_list}
    assert dispatched_page_ids == lost_page_ids, (
        f"expected only the 2 lost pages {lost_page_ids}, got {dispatched_page_ids}"
    )
    assert live_page_id not in dispatched_page_ids


@pytest.mark.asyncio
async def test_vision_pending_mid_flight_all_pages_in_flight_skips_entirely(
    in_memory_adapter,
) -> None:
    """Every PENDING page already has a live task -> skip the whole dispatch.

    Boundary of the page-scoped filter: when the in-flight set covers every
    remaining pending page, the compound dispatch list empties out and the
    branch must return None (counted skipped_healthy), not an empty
    compound dispatch.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-page-inflight")
    _create_vision_job_and_pages(
        in_memory_adapter,
        source_id="src-vp-page-inflight",
        page_count=3,
        completed=1,  # 2 PENDING remain -> mid-flight, not terminal
    )
    pending_rows = in_memory_adapter.list_vision_page_descriptions(
        "src-vp-page-inflight", statuses=[VisionPageStatus.PENDING]
    )
    queue.in_flight_vision_page_ids = AsyncMock(return_value={row["id"] for row in pending_rows})

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_pending_mid_flight_in_flight_check_failure_suppresses_dispatch(
    in_memory_adapter,
) -> None:
    """A queue-side error checking in-flight pages must suppress this pass entirely.

    Deliberately the OPPOSITE polarity from ``_classify_extracting``'s
    per-chunk fallback (which degrades to "dispatch everything" on a scan
    exception): that idiom is only safe for chunks because extract_chunk
    has a documented pre-work DB short-circuit. A vision page pays the
    billable LLM call BEFORE its CAS in ``update_vision_page_description``
    rejects a duplicate, so "assume nothing is in flight, dispatch
    everything" on a transient Valkey blip would re-open the exact
    money bug this filter exists to close. Mirrors
    ``_queue_has_task_for``'s assume-in-flight-on-error polarity
    (``queue_check_failed_assuming_in_flight``) instead: suppress, log,
    and let the ~60s reconcile pass retry.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_vision_page_ids = AsyncMock(side_effect=RuntimeError("valkey blip"))

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-check-failed")
    _create_vision_job_and_pages(in_memory_adapter, source_id="src-vp-check-failed", page_count=2)

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 0
    assert stats.skipped_healthy == 1
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_pending_stale_source_gets_redispatched(
    in_memory_adapter,
) -> None:
    """A vision_pending source whose heartbeat is older than the threshold is recovered.

    Mirrors the ``test_stale_source_still_gets_redispatched`` pattern for
    the indexing branch.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    queue.in_flight_vision_page_ids = AsyncMock(return_value=set())

    _seed_vision_pending_source(in_memory_adapter, source_id="src-vp-stale")
    _create_vision_job_and_pages(in_memory_adapter, source_id="src-vp-stale", page_count=2)
    # Last heartbeat 20 minutes ago — well past the 120s threshold.
    in_memory_adapter.update_source_last_activity(
        source_id="src-vp-stale",
        database_name="default",
        at_time=datetime.now(UTC) - timedelta(minutes=20),
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        stalled_threshold_seconds=120,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    assert queue.enqueue.await_count == 2  # 2 PENDING pages
    operations = [c.kwargs["operation"] for c in queue.enqueue.await_args_list]
    assert all(op == "vision_page" for op in operations)
