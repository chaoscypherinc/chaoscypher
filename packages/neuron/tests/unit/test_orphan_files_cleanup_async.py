# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify _orphan_files_cleanup_loop does not block the event loop.

Mirrors test_orphan_task_cleanup_async.py — the cleanup helper is sync
filesystem I/O, so the loop must drive it through asyncio.to_thread
to keep the event loop responsive.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_orphan_files_cleanup_does_not_block_event_loop(tmp_path: Path) -> None:
    """A slow synchronous filesystem sweep must not stall a concurrent task."""
    from chaoscypher_neuron.worker import _orphan_files_cleanup_loop

    adapter = MagicMock()

    def slow_cleanup(*, staging_dir, adapter, database_name, retention_seconds):  # type: ignore[no-untyped-def]
        time.sleep(0.5)  # simulate a slow filesystem walk
        return 0

    tick_count = 0

    async def ticker() -> None:
        nonlocal tick_count
        for _ in range(20):
            await asyncio.sleep(0.05)
            tick_count += 1

    with patch(
        "chaoscypher_core.services.sources.orphan_files.cleanup_orphan_source_files",
        side_effect=slow_cleanup,
    ):
        cleanup_task = asyncio.create_task(
            _orphan_files_cleanup_loop(
                adapter=adapter,
                staging_dir=tmp_path,
                database_name="default",
                retention_days=1,
                interval_seconds=0.01,
                pass_timeout_seconds=60,
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
