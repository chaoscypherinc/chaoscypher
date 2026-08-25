# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QueueService recovery counters + force_reconcile."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.queue.reconciler import ReconcileStats
from chaoscypher_core.queue.worker_timeouts import RECONCILER_SAFETY_MARGIN_SECONDS
from chaoscypher_cortex.features.queue.service import QueueService


@pytest.mark.asyncio
async def test_force_reconcile_single_queue_returns_stats() -> None:
    fake_stats = ReconcileStats(recovered_orphans=1, recovered_crashed=0, failed_unrecoverable=0)

    with patch(
        "chaoscypher_cortex.features.queue.service.reconcile_queue",
        new=AsyncMock(return_value=fake_stats),
    ):
        service = QueueService()
        # Fake a live queue_client with a mock Valkey connection so
        # _increment_recovery_counters has something to call.
        service.queue_client = MagicMock()
        service.queue_client.is_available = True
        service.queue_client.client = MagicMock()
        service.queue_client.client.hincrby = AsyncMock(return_value=1)

        result = await service.force_reconcile(queue_name="llm")

        assert result["recovered_orphans"] == 1
        assert result["recovered_crashed"] == 0
        assert result["failed_unrecoverable"] == 0


@pytest.mark.asyncio
async def test_force_reconcile_all_queues_merges_stats() -> None:
    """With an empty handler registry (the real Cortex condition — Cortex
    never calls register_handlers), force_reconcile must fall back to the
    canonical queues instead of silently reconciling nothing.
    """
    fake_llm = ReconcileStats(recovered_orphans=1)
    fake_ops = ReconcileStats(recovered_crashed=2)
    calls: list[str] = []

    async def fake_reconcile(client, queue_name, *, max_tries, timeout_seconds=None):
        calls.append(queue_name)
        return fake_llm if queue_name == "llm" else fake_ops

    with patch(
        "chaoscypher_cortex.features.queue.service.reconcile_queue",
        new=fake_reconcile,
    ):
        service = QueueService()
        service.queue_client = MagicMock()
        service.queue_client.is_available = True
        service.queue_client.queues = set()
        service.queue_client.client = MagicMock()
        service.queue_client.client.hincrby = AsyncMock(return_value=1)

        result = await service.force_reconcile(queue_name=None)

        assert sorted(calls) == ["llm", "operations"]
        assert result["recovered_orphans"] == 1
        assert result["recovered_crashed"] == 2


@pytest.mark.asyncio
async def test_get_recovery_counters_reads_from_valkey() -> None:
    service = QueueService()
    service.queue_client = MagicMock()
    service.queue_client.is_available = True
    service.queue_client.client = MagicMock()
    service.queue_client.client.hgetall = AsyncMock(
        return_value={
            b"recovered_orphans": b"3",
            b"recovered_crashed": b"1",
            b"failed_unrecoverable": b"0",
        }
    )

    counters = await service.get_recovery_counters("llm")

    assert counters["recovered_orphans"] == 3
    assert counters["recovered_crashed"] == 1
    assert counters["failed_unrecoverable"] == 0


@pytest.mark.asyncio
async def test_get_recovery_counters_returns_zero_when_absent() -> None:
    service = QueueService()
    service.queue_client = MagicMock()
    service.queue_client.is_available = True
    service.queue_client.client = MagicMock()
    service.queue_client.client.hgetall = AsyncMock(return_value={})

    counters = await service.get_recovery_counters("llm")
    assert counters == {
        "recovered_orphans": 0,
        "recovered_crashed": 0,
        "failed_unrecoverable": 0,
    }


@pytest.mark.asyncio
async def test_force_reconcile_cutoff_tracks_workers_yaml_not_settings_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safety net's cutoff must come from the worker's EFFECTIVE timeout.

    Cortex's ``_cortex_reconcile_safety_net_loop`` runs ``force_reconcile`` on a
    timer. It used to derive its absolute cutoff from
    ``settings.timeouts.*_worker_default`` while the worker's real deadline comes
    from ``/data/workers.yaml``. Because the reconciler's absolute-timeout branch
    is heartbeat-blind and ``requeue_atomic.lua`` refuses only
    completed/cancelled, an operator who raised the worker timeout got every long
    task reset to ``queued`` and dispatched to a second worker at the old default.
    """
    from chaoscypher_core.app_config import get_settings

    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    raised = 7200
    (tmp_path / "workers.yaml").write_text(f"llm_worker:\n  timeout: {raised}\n", encoding="utf-8")

    captured: dict[str, int | None] = {}

    async def fake_reconcile(client, queue_name, *, max_tries, timeout_seconds=None):
        """Record the cutoff each queue was reconciled with."""
        captured[queue_name] = timeout_seconds
        return ReconcileStats()

    with patch(
        "chaoscypher_cortex.features.queue.service.reconcile_queue",
        new=fake_reconcile,
    ):
        service = QueueService()
        service.queue_client = MagicMock()
        service.queue_client.is_available = True
        service.queue_client.queues = {"llm", "operations"}
        service.queue_client.client = MagicMock()
        service.queue_client.client.hincrby = AsyncMock(return_value=1)

        await service.force_reconcile(queue_name=None)

    assert captured["llm"] == raised + RECONCILER_SAFETY_MARGIN_SECONDS, (
        "the llm cutoff ignored the workers.yaml override — live tasks get requeued"
    )
    ops_default = get_settings().timeouts.operations_worker_default
    assert captured["operations"] == ops_default + RECONCILER_SAFETY_MARGIN_SECONDS


@pytest.mark.asyncio
async def test_force_reconcile_unavailable_returns_zero_stats() -> None:
    """When queue client is unavailable, returns zero counters (no raise)."""
    service = QueueService()
    service.queue_client = MagicMock()
    service.queue_client.is_available = False

    result = await service.force_reconcile(queue_name="llm")
    assert result == {
        "recovered_orphans": 0,
        "recovered_crashed": 0,
        "failed_unrecoverable": 0,
    }
