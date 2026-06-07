# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Heartbeat-refresher tests for the queue worker."""

from __future__ import annotations

import asyncio

import pytest

# Import for side-effect (configure_logging at import time)
import chaoscypher_neuron.worker  # noqa: F401
from chaoscypher_core.queue.worker import _heartbeat_refresher


@pytest.mark.asyncio
async def test_heartbeat_refresher_fires_at_interval() -> None:
    """_heartbeat_refresher calls refresh(task_id, ttl) on every interval."""
    refresh_calls: list[tuple[str, int]] = []

    async def fake_refresh(task_id: str, ttl: int) -> None:
        refresh_calls.append((task_id, ttl))

    task = asyncio.create_task(
        _heartbeat_refresher(
            task_id="t-123",
            refresh=fake_refresh,
            refresh_interval=0.02,
            ttl_seconds=10,
        )
    )

    # Let it run for ~5 intervals.
    await asyncio.sleep(0.11)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert 2 <= len(refresh_calls) <= 9, (
        f"Expected multiple refresh calls in 0.11s @ 0.02s interval; got {len(refresh_calls)}"
    )
    assert all(call == ("t-123", 10) for call in refresh_calls), refresh_calls


@pytest.mark.asyncio
async def test_heartbeat_refresher_stops_on_cancel() -> None:
    """Cancelling _heartbeat_refresher stops further refresh calls."""
    refresh_calls: list[tuple[str, int]] = []

    async def fake_refresh(task_id: str, ttl: int) -> None:
        refresh_calls.append((task_id, ttl))

    task = asyncio.create_task(
        _heartbeat_refresher(
            task_id="t-456",
            refresh=fake_refresh,
            refresh_interval=0.02,
            ttl_seconds=10,
        )
    )
    await asyncio.sleep(0.05)
    pre_cancel = len(refresh_calls)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Wait a bit more — no additional refresh calls should fire after cancel.
    await asyncio.sleep(0.1)
    post_wait = len(refresh_calls)
    assert post_wait == pre_cancel, (
        f"Cancel did not stop refresh; pre={pre_cancel}, post-wait={post_wait}"
    )
