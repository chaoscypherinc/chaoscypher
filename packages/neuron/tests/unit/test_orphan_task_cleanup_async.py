# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify _orphan_task_cleanup_loop does not block the event loop."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_orphan_cleanup_does_not_block_event_loop() -> None:
    """A slow synchronous adapter call must not stall a concurrent task."""
    from chaoscypher_neuron.worker import _orphan_task_cleanup_loop

    adapter = MagicMock()

    def slow_cleanup(older_than_seconds: int) -> int:
        time.sleep(0.5)  # simulate a slow DELETE
        return 0

    adapter.cleanup_orphaned_chunk_tasks.side_effect = slow_cleanup

    tick_count = 0

    async def ticker() -> None:
        nonlocal tick_count
        for _ in range(20):
            await asyncio.sleep(0.05)
            tick_count += 1

    cleanup_task = asyncio.create_task(
        _orphan_task_cleanup_loop(
            adapter=adapter,
            retention_days=7,
            interval_seconds=0.01,
        )
    )
    ticker_task = asyncio.create_task(ticker())

    try:
        await asyncio.wait_for(ticker_task, timeout=2.0)
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    # If the cleanup blocked the loop, tick_count would be near 1.
    # With asyncio.to_thread, the ticker continues to advance.
    assert tick_count >= 15, f"Event loop appears blocked: only {tick_count} ticks fired"
