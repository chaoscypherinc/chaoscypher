# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: finalize gate triggers on any terminal task status.

Before this fix, recovery.py dispatched finalize_extraction only when
``completed + failed >= total``.  Tasks that landed in ``cancelled`` or
``orphaned`` (both legal terminal states per TERMINAL_TASK_STATES) were
ignored, so jobs whose chunks ended in those states never finalized.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _make_adapter(
    job: dict[str, Any],
    non_terminal_task_rows: list[dict[str, Any]],
) -> MagicMock:
    """Return a mock adapter whose relevant methods return the given data."""
    adapter = MagicMock()
    adapter.get_system_state.return_value = {"processing_paused": False}
    adapter.get_active_extraction_job.return_value = job
    # list_extraction_tasks_by_status is called twice in the gate path:
    # once for the non-terminal query (pending/queued/running) and once
    # downstream for re-dispatch candidates (pending/queued/failed).
    # We return non-terminal rows for EVERY call so both paths behave
    # consistently with the scenario.
    adapter.list_extraction_tasks_by_status.return_value = non_terminal_task_rows
    return adapter


def _make_service(adapter: MagicMock) -> SourceRecovery:
    queue_client = AsyncMock()
    queue_client.task_exists_for_source = AsyncMock(return_value=False)
    return SourceRecovery(adapter=adapter, queue_client=queue_client)


def _source(status: str = "extracting") -> dict[str, Any]:
    return {
        "id": "src_term",
        "status": status,
        "is_paused": False,
        "recovery_attempts": 0,
        "current_extraction_job_id": "job_term",
        "extraction_complete": False,
        "filename": "test.pdf",
        "filepath": "/tmp/test.pdf",
        "file_type": "pdf",
        "file_size": 100,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completed", "failed", "cancelled", "orphaned", "running", "expect_finalize"),
    [
        # All terminal but mixed — must dispatch finalize
        (1, 1, 1, 1, 0, True),
        # All cancelled — must dispatch finalize
        (0, 0, 4, 0, 0, True),
        # All orphaned — must dispatch finalize
        (0, 0, 0, 4, 0, True),
        # Only completed + failed (legacy gate) — must dispatch finalize
        (2, 2, 0, 0, 0, True),
        # All completed — must dispatch finalize
        (4, 0, 0, 0, 0, True),
        # One still running — must NOT dispatch
        (2, 1, 0, 0, 1, False),
        # One pending — must NOT dispatch (3 terminal + 1 non-terminal running row)
        (3, 0, 0, 0, 1, False),
    ],
    ids=[
        "mixed_terminal",
        "all_cancelled",
        "all_orphaned",
        "completed_and_failed_legacy",
        "all_completed",
        "one_running",
        "one_pending",
    ],
)
async def test_finalize_gate_counts_all_terminal_states(
    completed: int,
    failed: int,
    cancelled: int,
    orphaned: int,
    running: int,
    expect_finalize: bool,
) -> None:
    """The finalize gate must trigger whenever ALL tasks are in TERMINAL_TASK_STATES.

    Regression for the two-status gate (completed + failed only) that ignored
    cancelled and orphaned tasks.
    """
    terminal_count = completed + failed + cancelled + orphaned
    total = terminal_count + running
    if total == 0:
        total = 4  # guard against degenerate case

    job: dict[str, Any] = {
        "id": "job_term",
        "total_chunks": total,
        "completed_chunks": completed,
        "failed_chunks": failed,
        "generate_embeddings": True,
    }

    # Non-terminal rows: any status NOT in TERMINAL_TASK_STATES
    non_terminal_rows: list[dict[str, Any]] = [
        {"id": f"t_run_{i}", "status": "running"} for i in range(running)
    ]

    adapter = _make_adapter(job, non_terminal_rows)
    service = _make_service(adapter)

    action = await service._classify(
        source=_source(),
        database_name="default",
    )

    if expect_finalize:
        assert action is not None, (
            f"Expected finalize_extraction dispatch for "
            f"completed={completed}, failed={failed}, cancelled={cancelled}, "
            f"orphaned={orphaned}, running={running}"
        )
        assert action.get("operation") == "finalize_extraction", (
            f"Expected operation='finalize_extraction', got {action.get('operation')!r}"
        )
    elif action is not None:
        assert action.get("operation") != "finalize_extraction", (
            f"Expected NO finalize_extraction dispatch for "
            f"completed={completed}, failed={failed}, running={running}"
        )
