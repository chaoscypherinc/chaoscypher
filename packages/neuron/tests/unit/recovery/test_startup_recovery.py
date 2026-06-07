# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for _run_startup_recovery — orchestration of the two startup-recovery
primitives (extraction-task + stuck-source recovery).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from structlog.testing import capture_logs

# WorkerHarness lives in tests/fixtures/ (on sys.path via conftest.py).
from worker_harness import WorkerHarness  # type: ignore[import-not-found]

# Import for side-effect (configure_logging at import time)
import chaoscypher_neuron.worker  # noqa: F401
from chaoscypher_neuron.worker import _run_startup_recovery


@pytest.mark.asyncio
async def test_run_startup_recovery_calls_both_primitives_in_order(
    worker_harness: WorkerHarness,
) -> None:
    """_run_startup_recovery awaits task-recovery then source-recovery, in order."""
    ctx = worker_harness.ctx
    call_order: list[str] = []

    async def fake_task_recovery(
        adapter: object, database_name: str, settings: object
    ) -> dict[str, int]:
        call_order.append("tasks")
        return {"recovered": 3, "skipped": 1, "failed": 0}

    async def fake_source_recovery(adapter: object, database_name: str) -> dict[str, int]:
        call_order.append("sources")
        return {"reset": 2, "marked_failed": 0}

    with (
        patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new=AsyncMock(side_effect=fake_task_recovery),
        ) as mock_tasks,
        patch(
            "chaoscypher_neuron.worker.recover_stuck_sources",
            new=AsyncMock(side_effect=fake_source_recovery),
        ) as mock_sources,
    ):
        await _run_startup_recovery(ctx)

    assert call_order == ["tasks", "sources"], f"Expected tasks-then-sources; got {call_order}"
    mock_tasks.assert_called_once()
    mock_sources.assert_called_once()
    # Tasks recovery is passed (adapter, database_name, settings); sources is (adapter, database_name).
    tasks_kwargs = mock_tasks.call_args.kwargs
    assert tasks_kwargs["database_name"] == "test_db"
    assert tasks_kwargs["settings"] is ctx["settings"]
    sources_kwargs = mock_sources.call_args.kwargs
    assert sources_kwargs["database_name"] == "test_db"


@pytest.mark.asyncio
async def test_run_startup_recovery_emits_structured_logs(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """_run_startup_recovery emits the three documented breadcrumbs."""
    ctx = worker_harness.ctx

    with (
        patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new=AsyncMock(return_value={"recovered": 0, "skipped": 0, "failed": 0}),
        ),
        patch(
            "chaoscypher_neuron.worker.recover_stuck_sources",
            new=AsyncMock(return_value={"reset": 0, "marked_failed": 0}),
        ),
        capture_logs() as captured,
    ):
        await _run_startup_recovery(ctx)

    events = [c.get("event") for c in captured]
    assert "extraction_recovery_starting" in events
    assert "extraction_task_recovery_completed" in events
    assert "extraction_source_recovery_completed" in events


@pytest.mark.asyncio
async def test_run_startup_recovery_skips_when_storage_adapter_missing(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """_run_startup_recovery returns early with a skip log when storage_adapter is None."""
    ctx = worker_harness.ctx
    # The harness populates storage_adapter; remove it to trigger the skip path.
    ctx_copy = dict(ctx)
    ctx_copy["storage_adapter"] = None

    with (
        patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new=AsyncMock(),
        ) as mock_tasks,
        patch(
            "chaoscypher_neuron.worker.recover_stuck_sources",
            new=AsyncMock(),
        ) as mock_sources,
        capture_logs() as captured,
    ):
        await _run_startup_recovery(ctx_copy)  # type: ignore[arg-type]

    mock_tasks.assert_not_called()
    mock_sources.assert_not_called()
    events = [c.get("event") for c in captured]
    assert "startup_recovery_skipped" in events
    # The skip log should also include a "reason" field.
    skip_log = next(c for c in captured if c.get("event") == "startup_recovery_skipped")
    assert skip_log.get("reason") == "no_storage_adapter"


@pytest.mark.asyncio
async def test_run_startup_recovery_catches_and_logs_primitive_failure(
    worker_harness: WorkerHarness,
    structlog_for_caplog: None,
) -> None:
    """A raise inside either recovery primitive is caught + logged, not re-raised."""
    ctx = worker_harness.ctx

    boom = RuntimeError("simulated recovery failure")

    with (
        patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new=AsyncMock(side_effect=boom),
        ),
        patch(
            "chaoscypher_neuron.worker.recover_stuck_sources",
            new=AsyncMock(),
        ),
        capture_logs() as captured,
    ):
        # _run_startup_recovery must NOT re-raise.
        await _run_startup_recovery(ctx)

    events = [c.get("event") for c in captured]
    assert "extraction_recovery_error" in events
    error_log = next(c for c in captured if c.get("event") == "extraction_recovery_error")
    assert error_log.get("error_type") == "RuntimeError"
    assert "simulated recovery failure" in error_log.get("error", "")
