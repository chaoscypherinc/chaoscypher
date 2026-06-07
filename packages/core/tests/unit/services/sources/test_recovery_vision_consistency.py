# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recovery scanner - counter-vs-row-states reconciliation.

Pins the resolution of the PR 2 "two-commit window" finding: when
``_handle_vision_page`` crashes after the row UPDATE commits but
before the ``vision_jobs`` counter commits, the scanner must
reconcile from the row states and enqueue finalize.

The PR 2 ``vision_pending`` recovery branch reads
``job.completed + job.failed >= job.total_pages`` to decide whether
to dispatch ``OP_VISION_FINALIZE``. If a worker crash leaves the
counter stale (e.g. row UPDATE committed but counter UPDATE never
fired), that check is falsey and the mid-flight sub-branch then
finds zero PENDING rows -> returns ``None`` -> classified
``skipped_healthy``. The source stalls in ``vision_pending``
forever despite every row being terminal.

The reconciliation pre-pass added in this PR recomputes
``completed`` / ``failed`` from the actual row states. When the rows
say terminal but the counter does not, the scanner enqueues
``OP_VISION_FINALIZE`` just like the existing terminal sub-branch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


# ---------------------------------------------------------------------------
# Helpers (mirror PR 2's test_recovery_vision_pending.py conventions)
# ---------------------------------------------------------------------------


def _seed_vision_pending_source(adapter, *, source_id: str) -> None:
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
) -> str:
    """Create a vision_job with ``page_count`` PENDING pages.

    Returns the vision_job id. Counters are left at 0/0/N — tests in
    this module simulate the two-commit window where row states
    diverge from the counter.
    """
    pages = [
        {
            "page_number": i + 1,
            "kind": VisionPageKind.PDF_PAGE,
            "image_path": f"/tmp/{source_id}_p{i + 1}.png",
        }
        for i in range(page_count)
    ]
    return adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)


def _age_source(adapter, *, source_id: str, seconds: int = 3600) -> None:
    """Backdate the source's last_activity_at past the stall threshold."""
    adapter.update_source_last_activity(
        source_id=source_id,
        database_name=adapter.database_name,
        at_time=datetime.now(UTC) - timedelta(seconds=seconds),
    )


# ---------------------------------------------------------------------------
# Reconciliation: counter stale, rows terminal -> enqueue finalize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_reconciles_when_counter_stale_but_rows_terminal(
    in_memory_adapter,
) -> None:
    """All page rows terminal, counter never caught up -> enqueue finalize.

    Simulates the two-commit crash: rows became SUCCEEDED + FAILED but
    the vision_jobs counter still reads 0/0/2 (worker crashed between
    the row UPDATE commit and the counter UPDATE commit).
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-reconcile"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-vp-reconcile"
    _seed_vision_pending_source(in_memory_adapter, source_id=source_id)
    job_id = _create_vision_job_and_pages(in_memory_adapter, source_id=source_id, page_count=2)

    page_ids = [r["id"] for r in in_memory_adapter.list_vision_page_descriptions(source_id)]

    # Rows: terminal. Counter: not updated. Two-commit crash window.
    in_memory_adapter.update_vision_page_description(
        page_id=page_ids[0],
        new_status=VisionPageStatus.SUCCEEDED,
        description="ok",
        finish_reason="stop",
        error_message=None,
    )
    in_memory_adapter.update_vision_page_description(
        page_id=page_ids[1],
        new_status=VisionPageStatus.FAILED,
        description=None,
        finish_reason=None,
        error_message="render_failed",
    )
    # Deliberately do NOT call increment_vision_job_completed_and_check —
    # this is the stale-counter scenario.

    _age_source(in_memory_adapter, source_id=source_id)

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1, f"Expected reconciliation dispatch, got stats={stats!r}"
    queue.enqueue.assert_awaited_once()
    call_kwargs = queue.enqueue.await_args.kwargs
    assert call_kwargs["operation"] == "vision_finalize", (
        f"Expected vision_finalize but enqueued {call_kwargs['operation']}"
    )
    assert call_kwargs["queue"] == "operations"
    assert call_kwargs["data"]["source_id"] == source_id
    assert call_kwargs["data"]["job_id"] == job_id
    assert call_kwargs["data"]["database_name"] == "default"


@pytest.mark.asyncio
async def test_recovery_reconciles_includes_truncated_in_completed(
    in_memory_adapter,
) -> None:
    """TRUNCATED rows count as completed for reconciliation.

    Per VisionPageStatus docs, TRUNCATED is a terminal "succeeded with
    truncation" state. The reconciliation must treat TRUNCATED + SUCCEEDED
    + FAILED as terminal when summing actual row outcomes.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-trunc"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-vp-trunc"
    _seed_vision_pending_source(in_memory_adapter, source_id=source_id)
    _create_vision_job_and_pages(in_memory_adapter, source_id=source_id, page_count=2)
    page_ids = [r["id"] for r in in_memory_adapter.list_vision_page_descriptions(source_id)]

    in_memory_adapter.update_vision_page_description(
        page_id=page_ids[0],
        new_status=VisionPageStatus.TRUNCATED,
        description="partial",
        finish_reason="length",
        error_message=None,
    )
    in_memory_adapter.update_vision_page_description(
        page_id=page_ids[1],
        new_status=VisionPageStatus.SUCCEEDED,
        description="ok",
        finish_reason="stop",
        error_message=None,
    )
    # Counter still 0/0/2 — two-commit crash.
    _age_source(in_memory_adapter, source_id=source_id)

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.recovered == 1
    queue.enqueue.assert_awaited_once()
    assert queue.enqueue.await_args.kwargs["operation"] == "vision_finalize"


@pytest.mark.asyncio
async def test_recovery_skips_when_counter_and_rows_agree(
    in_memory_adapter,
) -> None:
    """Counter and rows both say "1 pending, 1 failed" -> mid-flight dispatch.

    Falls through the reconciliation pre-pass and into the existing
    mid-flight sub-branch, which re-enqueues OP_VISION_PAGE for the
    one remaining PENDING row.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-mid"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-vp-midflight"
    _seed_vision_pending_source(in_memory_adapter, source_id=source_id)
    job_id = _create_vision_job_and_pages(in_memory_adapter, source_id=source_id, page_count=2)
    page_ids = [r["id"] for r in in_memory_adapter.list_vision_page_descriptions(source_id)]

    # One row terminal, counter agrees. The other row stays PENDING.
    in_memory_adapter.update_vision_page_description(
        page_id=page_ids[0],
        new_status=VisionPageStatus.FAILED,
        description=None,
        finish_reason=None,
        error_message="x",
    )
    in_memory_adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.FAILED
    )

    _age_source(in_memory_adapter, source_id=source_id)

    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)
    stats = await recovery.reconcile_database(database_name="default")

    # Mid-flight: re-enqueue the one remaining PENDING page, NOT finalize.
    assert stats.recovered == 1
    enqueued_ops = [call.kwargs["operation"] for call in queue.enqueue.await_args_list]
    assert "vision_page" in enqueued_ops
    assert "vision_finalize" not in enqueued_ops, (
        f"Expected mid-flight vision_page dispatch only, got {enqueued_ops}"
    )
