# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QueueWorker._startup_reconcile."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.queue.reconciler import ReconcileStats
from chaoscypher_core.queue.worker import QueueWorker


def _build_worker() -> QueueWorker:
    """Build a QueueWorker with minimal config (two queues)."""
    return QueueWorker(
        client=MagicMock(),
        queues_config={
            "llm": {"concurrency": 1, "max_tries": 5, "timeout": 60},
            "operations": {"concurrency": 8, "max_tries": 5, "timeout": 60},
        },
        handlers={"llm": {}, "operations": {}},
    )


@pytest.mark.asyncio
async def test_startup_reconcile_skipped_when_queue_client_missing() -> None:
    """When _queue_client is None, the method returns cleanly."""
    worker = _build_worker()
    worker._queue_client = None

    # Should not raise; should return None without touching anything
    result = await worker._startup_reconcile()
    assert result is None


@pytest.mark.asyncio
async def test_startup_reconcile_calls_reconcile_queue_per_queue(
    monkeypatch,
) -> None:
    """Each configured queue gets a reconcile_queue call with its max_tries."""
    calls: list[tuple[str, int]] = []

    async def fake_reconcile(client, queue_name, *, max_tries, timeout_seconds=None):
        calls.append((queue_name, max_tries))
        return ReconcileStats()

    monkeypatch.setattr(
        "chaoscypher_core.queue.worker.reconcile_queue",
        fake_reconcile,
    )

    worker = _build_worker()
    worker._queue_client = MagicMock()

    await worker._startup_reconcile()

    # Both queues reconciled, each with its own max_tries from config
    assert ("llm", 5) in calls
    assert ("operations", 5) in calls
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_startup_reconcile_survives_per_queue_errors(monkeypatch) -> None:
    """If reconcile_queue raises for one queue, the other still runs."""
    call_log: list[str] = []

    async def fake_reconcile(client, queue_name, *, max_tries, timeout_seconds=None):
        call_log.append(queue_name)
        if queue_name == "llm":
            msg = "boom"
            raise RuntimeError(msg)
        return ReconcileStats()

    monkeypatch.setattr(
        "chaoscypher_core.queue.worker.reconcile_queue",
        fake_reconcile,
    )

    worker = _build_worker()
    worker._queue_client = MagicMock()

    # Should not raise — errors are logged and swallowed
    await worker._startup_reconcile()

    assert "llm" in call_log
    assert "operations" in call_log
