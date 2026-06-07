# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the worker's periodic reconciliation loop."""

import asyncio
import contextlib
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.queue.reconciler import ReconcileStats
from chaoscypher_core.queue.worker import QueueWorker


def _build_worker() -> QueueWorker:
    return QueueWorker(
        client=MagicMock(),
        queues_config={
            "llm": {"concurrency": 1, "max_tries": 5, "timeout": 60},
            "operations": {"concurrency": 8, "max_tries": 5, "timeout": 60},
        },
        handlers={"llm": {}, "operations": {}},
    )


@pytest.mark.asyncio
async def test_reconcile_loop_calls_reconciler_periodically(
    monkeypatch,
) -> None:
    """The loop invokes reconcile_queue every interval until cancelled."""
    call_log: list[str] = []

    async def fake_reconcile(client, queue_name, *, max_tries, timeout_seconds=None):
        call_log.append(queue_name)
        return ReconcileStats()

    monkeypatch.setattr(
        "chaoscypher_core.queue.worker.reconcile_queue",
        fake_reconcile,
    )

    worker = _build_worker()
    worker._queue_client = MagicMock()
    worker._reconcile_interval_seconds = 0.05  # 50ms for test
    worker._running = True

    loop_task = asyncio.create_task(worker._reconcile_loop())

    # Let the loop run ~4 iterations (200ms at 50ms interval)
    await asyncio.sleep(0.2)

    worker._running = False
    loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop_task

    # Should have seen both queues multiple times
    assert call_log.count("llm") >= 2
    assert call_log.count("operations") >= 2


@pytest.mark.asyncio
async def test_reconcile_loop_skipped_when_queue_client_missing() -> None:
    """With _queue_client=None, the loop exits without doing anything."""
    worker = _build_worker()
    worker._queue_client = None
    worker._reconcile_interval_seconds = 0.05
    worker._running = True

    # Should return immediately without error
    await asyncio.wait_for(worker._reconcile_loop(), timeout=0.5)


@pytest.mark.asyncio
async def test_reconcile_loop_survives_errors(monkeypatch) -> None:
    """Transient errors in reconcile_queue don't kill the loop."""
    target_calls = 3
    call_count = 0
    # Signalled once the loop has driven reconcile_queue through the two
    # error-raising calls AND a subsequent success. Waiting on this event
    # (instead of asserting a count after a fixed sleep) makes the test
    # deterministic on slow/parallel CI runners where wall-clock timing
    # would otherwise yield too few iterations.
    reached_target = asyncio.Event()

    async def flaky_reconcile(client, queue_name, *, max_tries, timeout_seconds=None):
        nonlocal call_count
        call_count += 1
        if call_count >= target_calls:
            reached_target.set()
        if call_count < target_calls:
            msg = "transient"
            raise RuntimeError(msg)
        return ReconcileStats()

    monkeypatch.setattr(
        "chaoscypher_core.queue.worker.reconcile_queue",
        flaky_reconcile,
    )

    worker = _build_worker()
    worker._queue_client = MagicMock()
    # Near-zero interval so iterations are bounded by event delivery, not the
    # sleep; the assertion below waits on the event rather than the clock.
    worker._reconcile_interval_seconds = 0
    worker._running = True

    loop_task = asyncio.create_task(worker._reconcile_loop())
    try:
        # Wait for the loop to survive both errors and complete the success
        # call. Generous timeout guards against a genuine hang on any runner.
        await asyncio.wait_for(reached_target.wait(), timeout=5.0)
    finally:
        worker._running = False
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task

    # Loop kept calling reconcile_queue across the transient errors.
    assert call_count >= target_calls
