# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recovery scanner - vision_job-aware indexing dispatch.

Pins the resolution of the PR 2 "finalizer CAS-then-enqueue window"
finding: when ``vision_finalizer`` crashes between the
``VISION_PENDING -> INDEXING`` CAS and the resume-task enqueue, the
recovery scanner must not re-run the indexing handler from scratch -
that would double-count loader counters and orphan the existing
vision_jobs row.

The fix lives inside the pending/indexing branch in ``_classify``:
when a vision_job exists for the source, route to ``OP_VISION_FINALIZE``
instead of ``OP_INDEX_DOCUMENT``. The finalizer's own idempotency
(status-check + CAS) handles every sub-case:

- VISION_PENDING -> finish the work + enqueue resume.
- INDEXING already -> re-emit the resume task.
- Already advanced -> skip cleanly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.recovery import SourceRecovery
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


# ---------------------------------------------------------------------------
# Helpers (mirror PR 2's test_recovery_vision_pending.py conventions)
# ---------------------------------------------------------------------------


def _seed_indexing_source(adapter, *, source_id: str) -> None:
    """Insert a minimal source row in status=indexing.

    Mirrors the post-CAS state left by ``vision_finalizer`` after it
    transitions a source from VISION_PENDING to INDEXING but before
    it has enqueued the resume task.
    """
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 1024,
            "content_hash": f"hash-{source_id}",
            "status": "indexing",
        }
    )


def _age_source(adapter, *, source_id: str, seconds: int = 3600) -> None:
    """Backdate the source's last_activity_at past the stall threshold."""
    adapter.update_source_last_activity(
        source_id=source_id,
        database_name=adapter.database_name,
        at_time=datetime.now(UTC) - timedelta(seconds=seconds),
    )


# ---------------------------------------------------------------------------
# Primary case: terminal vision_job + INDEXING source -> finalize dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_indexing_with_terminal_vision_job_dispatches_finalize(
    in_memory_adapter,
) -> None:
    """Source is in INDEXING with a terminal vision_job -> enqueue
    OP_VISION_FINALIZE, not OP_INDEX_DOCUMENT.

    Simulates the finalizer CAS-then-crash window: the vision pages
    all succeeded, the finalizer CAS'd status to INDEXING, but the
    process crashed before the resume task landed on the queue. The
    next recovery scan must route to ``OP_VISION_FINALIZE`` so the
    finalizer's idempotent path can re-emit the resume task; it must
    NOT re-dispatch ``OP_INDEX_DOCUMENT`` (that would run a full
    first-pass, double-counting loader quality counters and orphaning
    the existing vision_jobs row).
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-finalize"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-idx-vf-terminal"
    _seed_indexing_source(in_memory_adapter, source_id=source_id)

    # Single-page vision job that already finished successfully.
    job_id = in_memory_adapter.create_vision_job_with_pages(
        source_id=source_id,
        pages=[
            {
                "page_number": 1,
                "kind": VisionPageKind.PDF_PAGE,
                "image_path": f"/tmp/{source_id}_p1.png",
            }
        ],
    )
    page_id = in_memory_adapter.list_vision_page_descriptions(source_id)[0]["id"]
    in_memory_adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.SUCCEEDED,
        description="ok",
        finish_reason="stop",
        error_message=None,
    )
    in_memory_adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
    )

    _age_source(in_memory_adapter, source_id=source_id)

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1, f"Expected dispatch, got stats={stats!r}"
    enqueued_ops = [call.kwargs["operation"] for call in queue.enqueue.await_args_list]
    assert "vision_finalize" in enqueued_ops, (
        f"Expected vision_finalize on indexing+terminal_vision_job dispatch; saw {enqueued_ops}"
    )
    # Critical: must NOT re-dispatch OP_INDEX_DOCUMENT (would double-count
    # loader counters and orphan the existing vision_job).
    assert "index_document" not in enqueued_ops, (
        f"Recovery must not re-dispatch index_document when a vision_job exists; saw {enqueued_ops}"
    )

    queue.enqueue.assert_awaited_once()
    call_kwargs = queue.enqueue.await_args.kwargs
    assert call_kwargs["queue"] == "operations"
    assert call_kwargs["data"]["source_id"] == source_id
    assert call_kwargs["data"]["job_id"] == job_id
    assert call_kwargs["data"]["database_name"] == "default"


# ---------------------------------------------------------------------------
# Mid-flight vision_job + INDEXING source -> still routes to finalize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_indexing_with_non_terminal_vision_job_dispatches_finalize(
    in_memory_adapter,
) -> None:
    """Source in INDEXING with a mid-flight vision_job still routes to finalize.

    The finalizer's own idempotency (status check + CAS) handles every
    sub-case, so the cleanest semantics for the recovery branch is:
    always route to ``OP_VISION_FINALIZE`` when a vision_job row exists
    for an INDEXING source. The finalizer will detect a non-terminal
    job and skip cleanly without enqueueing a resume task.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-finalize"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-idx-vf-midflight"
    _seed_indexing_source(in_memory_adapter, source_id=source_id)

    # Two pages, one finished, one still PENDING -> counter 1/0/2, not terminal.
    job_id = in_memory_adapter.create_vision_job_with_pages(
        source_id=source_id,
        pages=[
            {
                "page_number": 1,
                "kind": VisionPageKind.PDF_PAGE,
                "image_path": f"/tmp/{source_id}_p1.png",
            },
            {
                "page_number": 2,
                "kind": VisionPageKind.PDF_PAGE,
                "image_path": f"/tmp/{source_id}_p2.png",
            },
        ],
    )
    page_ids = [r["id"] for r in in_memory_adapter.list_vision_page_descriptions(source_id)]
    in_memory_adapter.update_vision_page_description(
        page_id=page_ids[0],
        new_status=VisionPageStatus.SUCCEEDED,
        description="ok",
        finish_reason="stop",
        error_message=None,
    )
    in_memory_adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
    )

    _age_source(in_memory_adapter, source_id=source_id)

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1, f"Expected dispatch, got stats={stats!r}"
    enqueued_ops = [call.kwargs["operation"] for call in queue.enqueue.await_args_list]
    assert "vision_finalize" in enqueued_ops, (
        f"Expected vision_finalize on indexing+non-terminal_vision_job dispatch; saw {enqueued_ops}"
    )
    assert "index_document" not in enqueued_ops


# ---------------------------------------------------------------------------
# Negative case: INDEXING source with no vision_job -> existing branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_indexing_without_vision_job_uses_existing_branch(
    in_memory_adapter,
) -> None:
    """Source in INDEXING with no vision_job -> existing pending/indexing branch.

    Pins that the new vision_job-aware route only fires when a
    vision_job actually exists. For a plain text source that crashed
    mid-indexing, recovery must still dispatch ``OP_INDEX_DOCUMENT``
    on the operations queue.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-idx"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-idx-no-vision"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "txt",
            "file_size": 64,
            "content_hash": f"hash-{source_id}",
            "status": "indexing",
        }
    )

    _age_source(in_memory_adapter, source_id=source_id)

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1, f"Expected dispatch, got stats={stats!r}"
    enqueued_ops = [call.kwargs["operation"] for call in queue.enqueue.await_args_list]
    assert "index_document" in enqueued_ops, (
        f"Expected index_document on indexing+no_vision_job dispatch; saw {enqueued_ops}"
    )
    assert "vision_finalize" not in enqueued_ops, (
        f"Recovery must not route to vision_finalize when no vision_job exists; saw {enqueued_ops}"
    )


# ---------------------------------------------------------------------------
# End-to-end: scanner dispatch + finalizer execution actually makes progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_indexing_with_vision_job_actually_makes_progress_after_dispatch(
    in_memory_adapter,
) -> None:
    """End-to-end pin for the Task 2 + finalizer fix combined.

    Without the finalizer's extended INDEXING-aware idempotency branch,
    the scanner's vision_finalize dispatch was a no-op: the finalizer
    bailed with skipped_already_advanced and the source stalled
    permanently. This test simulates the full chain — scanner dispatches
    finalize, the dispatched payload feeds handle_vision_finalize, and
    the finalizer re-emits OP_INDEX_DOCUMENT — and asserts forward
    progress is actually made.
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-finalize"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-idx-e2e-progress"
    _seed_indexing_source(in_memory_adapter, source_id=source_id)

    job_id = in_memory_adapter.create_vision_job_with_pages(
        source_id=source_id,
        pages=[
            {
                "page_number": 1,
                "kind": VisionPageKind.PDF_PAGE,
                "image_path": f"/tmp/{source_id}_p1.png",
            }
        ],
    )
    page_id = in_memory_adapter.list_vision_page_descriptions(source_id)[0]["id"]
    in_memory_adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.SUCCEEDED,
        description="ok",
        finish_reason="stop",
        error_message=None,
    )
    in_memory_adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
    )

    _age_source(in_memory_adapter, source_id=source_id)

    # ---- Step 1: scanner dispatches OP_VISION_FINALIZE -------------------
    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")
    assert stats.recovered == 1

    finalize_calls = [
        call
        for call in queue.enqueue.await_args_list
        if call.kwargs["operation"] == "vision_finalize"
    ]
    assert len(finalize_calls) == 1, (
        f"Expected exactly one vision_finalize dispatch; saw {queue.enqueue.await_args_list}"
    )
    finalize_payload = finalize_calls[0].kwargs["data"]

    # ---- Step 2: feed the dispatched payload into handle_vision_finalize -
    # Source row reports status=indexing (the post-CAS-pre-enqueue stall).
    src = in_memory_adapter.get_source(source_id, "default")
    assert src is not None
    assert src["status"] == SourceStatus.INDEXING.value

    settings = MagicMock()
    with (
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
            new_callable=AsyncMock,
        ) as mock_enqueue,
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer.queue_client.task_exists_for_source",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        result = await handle_vision_finalize(
            data=finalize_payload,
            adapter=in_memory_adapter,
            settings=settings,
        )

    # The finalizer recognised the stall and re-emitted the resume task.
    assert result["status"] == "re_emitted_resume", (
        f"Finalizer must converge an INDEXING+vision_job source; got {result!r}"
    )
    mock_enqueue.assert_awaited_once()
    kwargs = mock_enqueue.call_args.kwargs
    assert kwargs["source_id"] == source_id
    assert kwargs["database_name"] == "default"
    assert kwargs["file_info"]["filepath"] == f"/tmp/{source_id}.pdf"
