# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for transactional structure of finalize_extraction's terminal writes.

The finalize handler's terminal block writes ``complete_extraction``
(marks source ``extracted``) + ``complete_extraction_job`` (closes the
job) atomically inside ``adapter.transaction()``. The
``_queue_commit_phase`` Valkey enqueue runs AFTER the transaction
closes (2026-05-20 writer-lock-contention root fix) so the SQLite
writer lock is not held across the Valkey roundtrip.

If the enqueue fails after the DB commit succeeds, the source sits at
``status='extracted'`` with no commit task —
``SourceRecovery._classify_extracted`` (services/sources/recovery.py:840)
detects exactly this case via ``_queue_has_task_for`` and auto-
dispatches a commit task at the next reconcile pass. The atomicity
contract therefore moves from "DB writes + enqueue together" to "DB
writes atomic; enqueue best-effort with reconciler self-heal."
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_tracking_adapter(events: list[str]) -> MagicMock:
    """Build a SqliteAdapter mock that records call order against ``events``."""
    adapter = MagicMock()

    @contextmanager
    def _tracking_transaction():
        events.append("tx_enter")
        try:
            yield
            events.append("tx_exit_ok")
        except BaseException:
            events.append("tx_exit_err")
            raise

    adapter.transaction = _tracking_transaction

    adapter.get_source.return_value = {
        "id": "src-1",
        "status": "extracting",
        "is_paused": False,
    }
    adapter.get_system_state.return_value = None
    adapter.get_chunk_tasks_by_job.return_value = []
    adapter.get_extraction_job.return_value = {
        "id": "job-1",
        "detected_domain": None,
        "forced_domain": None,
    }
    adapter.get_oldest_waiting_extraction.return_value = None
    adapter.get_file.return_value = {"id": "src-1", "filename": "test.txt"}
    adapter.start_extraction_job = MagicMock()

    adapter.complete_extraction = MagicMock(
        side_effect=lambda *a, **kw: events.append("complete_extraction")
    )
    adapter.complete_extraction_job = MagicMock(
        side_effect=lambda *a, **kw: events.append("complete_extraction_job")
    )

    return adapter


@pytest.mark.asyncio
async def test_empty_result_path_db_writes_atomic_enqueue_outside_transaction() -> None:
    """Empty extraction (zero entities): DB writes atomic inside one transaction,
    Valkey enqueue runs AFTER the transaction closes.

    Sequence under test: ``get_completed_chunk_results`` returns ``[]`` →
    handler routes to the empty-result branch, which writes
    ``complete_extraction`` + ``complete_extraction_job`` inside one
    ``adapter.transaction()``, then enqueues the commit phase via
    ``queue_import_commit`` OUTSIDE that transaction.

    This pins the 2026-05-20 hoist: the writer lock must not be held
    across the Valkey roundtrip. The reconciler at
    ``services/sources/recovery.py:840`` self-heals if the post-txn
    enqueue fails.
    """
    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    events: list[str] = []
    adapter = _make_tracking_adapter(events)
    adapter.get_completed_chunk_results.return_value = []

    queue_import_commit = AsyncMock(side_effect=lambda **kw: events.append("queue_import_commit"))

    with (
        patch(
            "chaoscypher_core.operations.queue_utils.queue_import_commit",
            new=queue_import_commit,
        ),
        patch(
            "chaoscypher_core.operations.queue_utils.queue_import_analysis",
            new=AsyncMock(),
        ),
    ):
        await finalize_extraction_handler(
            graph_repository=MagicMock(),
            llm_service=AsyncMock(),
            source_repository=adapter,
            chunk_extraction_service=MagicMock(),
            data={
                "source_id": "src-1",
                "job_id": "job-1",
                "database_name": "default",
            },
        )

    assert "tx_enter" in events, f"adapter.transaction() never entered; events={events}"
    tx_enter = events.index("tx_enter")
    tx_exit = next(
        (i for i, e in enumerate(events) if e in ("tx_exit_ok", "tx_exit_err")),
        None,
    )
    assert tx_exit is not None and tx_exit > tx_enter, (
        f"transaction must close before any subsequent work; events={events}"
    )
    assert events[tx_exit] == "tx_exit_ok", (
        f"empty-result happy path must exit transaction cleanly; events={events}"
    )

    # DB writes are inside the transaction.
    for required in ("complete_extraction", "complete_extraction_job"):
        assert required in events, f"{required} not called; events={events}"
        idx = events.index(required)
        assert tx_enter < idx < tx_exit, (
            f"{required} must occur inside transaction; events={events}"
        )

    # Valkey enqueue fires AFTER the transaction closes — writer lock is
    # released before the Valkey roundtrip starts. This is the 2026-05-20
    # writer-lock-contention root fix.
    assert "queue_import_commit" in events, f"enqueue never fired; events={events}"
    enqueue_idx = events.index("queue_import_commit")
    assert enqueue_idx > tx_exit, (
        f"queue_import_commit must fire OUTSIDE the transaction so the "
        f"writer lock is not held across the Valkey roundtrip "
        f"(2026-05-20 writer-lock-contention root fix); events={events}"
    )


@pytest.mark.asyncio
async def test_empty_result_path_enqueue_failure_keeps_extracted_status() -> None:
    """A failed Valkey enqueue must NOT roll back the DB writes.

    New contract (2026-05-20 hoist): once the inner transaction commits,
    the source row is at ``status='extracted'`` even if the subsequent
    Valkey enqueue fails. The half-state ("extracted" without a commit
    task in queue) is then picked up by
    ``SourceRecovery._classify_extracted`` (services/sources/recovery.py:840)
    which auto-dispatches a fresh commit task at the next reconcile pass.

    This test pins:
      - The transaction exits cleanly (``tx_exit_ok``), NOT via the error
        path. The Valkey failure does not propagate into the DB session.
      - The ``RuntimeError`` from the enqueue still bubbles up so the
        queue worker classifies it (typically as transient) and the
        handler can retry.
      - ``complete_extraction`` has executed (DB row is now 'extracted').
    """
    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    events: list[str] = []
    adapter = _make_tracking_adapter(events)
    adapter.get_completed_chunk_results.return_value = []
    adapter.fail_extraction_job = MagicMock()
    adapter.fail_extraction = MagicMock()

    queue_import_commit = AsyncMock(side_effect=RuntimeError("valkey down"))

    with (
        patch(
            "chaoscypher_core.operations.queue_utils.queue_import_commit",
            new=queue_import_commit,
        ),
        patch(
            "chaoscypher_core.operations.queue_utils.queue_import_analysis",
            new=AsyncMock(),
        ),
    ):
        with pytest.raises(RuntimeError, match="valkey down"):
            await finalize_extraction_handler(
                graph_repository=MagicMock(),
                llm_service=AsyncMock(),
                source_repository=adapter,
                chunk_extraction_service=MagicMock(),
                data={
                    "source_id": "src-1",
                    "job_id": "job-1",
                    "database_name": "default",
                },
            )

    assert "tx_enter" in events, f"transaction never entered; events={events}"
    assert "tx_exit_ok" in events, (
        f"transaction must exit cleanly even when the post-txn enqueue raises — "
        f"DB writes are durable, the reconciler self-heals from status='extracted'. "
        f"events={events}"
    )
    assert "tx_exit_err" not in events, (
        f"DB transaction must NOT roll back on Valkey failure — that would "
        f"undo the durable status='extracted' transition and force re-extraction. "
        f"events={events}"
    )
    tx_enter = events.index("tx_enter")
    tx_exit_ok = events.index("tx_exit_ok")
    assert tx_enter < events.index("complete_extraction") < tx_exit_ok, (
        f"complete_extraction must persist inside the transaction; events={events}"
    )
