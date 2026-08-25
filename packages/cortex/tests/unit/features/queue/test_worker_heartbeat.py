# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that QueueWorker manages per-task heartbeats around handler execution."""

import asyncio

import pytest

from chaoscypher_core.queue.worker import _run_with_heartbeat


@pytest.mark.asyncio
async def test_run_with_heartbeat_refreshes_during_handler() -> None:
    """The heartbeat refresher fires repeatedly while the handler runs."""
    target_refreshes = 3
    refresh_calls: list[float] = []
    # Signalled once the refresher has fired the target number of times. The
    # handler blocks on this event rather than sleeping a fixed wall-clock
    # window, so the test is deterministic on slow/parallel CI runners where
    # a 250ms-vs-50ms margin would otherwise yield too few refreshes.
    reached_target = asyncio.Event()

    async def fake_refresh(task_id: str, ttl_seconds: int) -> None:
        refresh_calls.append(asyncio.get_running_loop().time())
        if len(refresh_calls) >= target_refreshes:
            reached_target.set()

    async def slow_handler() -> str:
        # Generous ceiling: it only bites if the refresher never runs, which
        # is exactly the regression this test exists to catch.
        await asyncio.wait_for(reached_target.wait(), timeout=5.0)
        return "done"

    result = await _run_with_heartbeat(
        task_id="abc-123",
        coro_factory=slow_handler,
        refresh=fake_refresh,
        # Near-zero interval so refreshes are bounded by event-loop turns,
        # not the clock.
        refresh_interval=0,
        ttl_seconds=1,
    )

    assert result == "done"
    assert len(refresh_calls) >= target_refreshes, (
        f"Expected at least {target_refreshes} heartbeat refreshes while the "
        f"handler ran, got {len(refresh_calls)}"
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
