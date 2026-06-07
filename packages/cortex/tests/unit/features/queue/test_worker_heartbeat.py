# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that QueueWorker manages per-task heartbeats around handler execution."""

import asyncio

import pytest

from chaoscypher_core.queue.worker import _run_with_heartbeat


@pytest.mark.asyncio
async def test_run_with_heartbeat_refreshes_during_handler() -> None:
    """The heartbeat refresher fires at least twice during a slow handler."""
    refresh_calls: list[float] = []

    async def fake_refresh(task_id: str, ttl_seconds: int) -> None:
        refresh_calls.append(asyncio.get_event_loop().time())

    async def slow_handler() -> str:
        await asyncio.sleep(0.25)  # 250ms
        return "done"

    result = await _run_with_heartbeat(
        task_id="abc-123",
        coro_factory=slow_handler,
        refresh=fake_refresh,
        refresh_interval=0.05,  # 50ms
        ttl_seconds=1,
    )

    assert result == "done"
    # At ~250ms / 50ms, expect at least 3 refreshes (allow slack for timing)
    assert len(refresh_calls) >= 3, (
        f"Expected at least 3 heartbeat refreshes in 250ms at 50ms interval, "
        f"got {len(refresh_calls)}"
    )


@pytest.mark.asyncio
async def test_run_with_heartbeat_stops_refresh_on_handler_success() -> None:
    """Once the handler returns, the refresher task is cancelled cleanly."""
    refresh_calls = 0

    async def fake_refresh(task_id: str, ttl_seconds: int) -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    async def fast_handler() -> str:
        return "done"

    await _run_with_heartbeat(
        task_id="abc-123",
        coro_factory=fast_handler,
        refresh=fake_refresh,
        refresh_interval=0.05,
        ttl_seconds=1,
    )
    # Give the refresher a moment — it should NOT continue firing
    await asyncio.sleep(0.15)
    initial = refresh_calls
    await asyncio.sleep(0.15)
    assert refresh_calls == initial, "Heartbeat refresher should stop after handler completes"


@pytest.mark.asyncio
async def test_run_with_heartbeat_propagates_handler_exception() -> None:
    """Exceptions from the handler are re-raised to the caller."""

    async def fake_refresh(task_id: str, ttl_seconds: int) -> None:
        pass

    async def failing_handler() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        await _run_with_heartbeat(
            task_id="abc-123",
            coro_factory=failing_handler,
            refresh=fake_refresh,
            refresh_interval=0.05,
            ttl_seconds=1,
        )
