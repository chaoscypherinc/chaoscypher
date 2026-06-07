# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QueueService recovery counters + force_reconcile."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.queue.reconciler import ReconcileStats
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
        service.queue_client.queues = {"llm", "operations"}
        service.queue_client.client = MagicMock()
        service.queue_client.client.hincrby = AsyncMock(return_value=1)

        result = await service.force_reconcile(queue_name=None)

        assert len(calls) == 2
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
