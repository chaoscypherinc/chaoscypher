# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: queue scan failure causes recovery to skip dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.client import QueueClient


@pytest.mark.asyncio
async def test_task_exists_for_source_reraises_on_scan_failure() -> None:
    """A scan exception bubbles instead of returning False."""
    client = QueueClient.__new__(QueueClient)
    client._connected = True

    fake_redis = MagicMock()

    async def boom(*args, **kwargs):
        raise RuntimeError("valkey scan exploded")
        yield  # never reached, makes this an async generator

    fake_redis.scan_iter = boom
    client.client = fake_redis

    with pytest.raises(RuntimeError, match="valkey scan exploded"):
        await client.task_exists_for_source(
            source_id="src1",
            database_name="default",
            operations=["op_index_document"],
        )


@pytest.mark.asyncio
async def test_recovery_wrapper_skips_dispatch_on_scan_failure() -> None:
    """SourceRecovery._queue_has_task_for returns True on queue scan error."""
    from chaoscypher_core.services.sources.recovery import SourceRecovery

    queue_client = AsyncMock()
    queue_client.task_exists_for_source = AsyncMock(side_effect=RuntimeError("boom"))
    adapter = MagicMock()

    service = SourceRecovery(adapter=adapter, queue_client=queue_client)
    result = await service._queue_has_task_for(
        source_id="src1",
        database_name="default",
        operations=("op_index_document",),
    )
    # Skip-on-error policy: report "task exists" so recovery doesn't
    # re-dispatch a duplicate when a real one might already be queued.
    assert result is True
