# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Cortex-side safety-net queue reconciliation."""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cortex.lifespan import _cortex_reconcile_safety_net_loop


@pytest.mark.asyncio
async def test_safety_net_loop_fires_periodically() -> None:
    """The loop invokes force_reconcile repeatedly until shutdown."""
    target_calls = 2
    calls: list[str] = []
    # Signalled once the loop has driven force_reconcile the target number of
    # times. Waiting on this event (instead of asserting a count after a fixed
    # real sleep) makes the test deterministic on slow/parallel CI runners
    # where wall-clock timing would otherwise yield too few iterations.
    reached_target = asyncio.Event()

    async def fake_force_reconcile(queue_name=None):
        calls.append(queue_name or "all")
        if len(calls) >= target_calls:
            reached_target.set()
        return {
            "recovered_orphans": 0,
            "recovered_crashed": 0,
            "failed_unrecoverable": 0,
        }

    service = MagicMock()
    service.force_reconcile = AsyncMock(side_effect=fake_force_reconcile)

    shutdown = {"value": False}

    async def should_shutdown() -> bool:
        return shutdown["value"]

    loop_task = asyncio.create_task(
        _cortex_reconcile_safety_net_loop(
            service=service,
            # Near-zero interval so iterations are bounded by event delivery,
            # not the clock; the wait below is on the event, not a sleep.
            interval_seconds=0,
            should_shutdown=should_shutdown,
        )
    )
    try:
        # Generous ceiling: it only bites if the loop is genuinely broken.
        await asyncio.wait_for(reached_target.wait(), timeout=5.0)
    finally:
        shutdown["value"] = True
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task

    # Should have invoked force_reconcile multiple times
    assert len(calls) >= target_calls
    assert all(c == "all" for c in calls)


@pytest.mark.asyncio
async def test_safety_net_survives_errors(
    structlog_for_caplog: None,  # pytest fixture, side-effect only
) -> None:
    """A transient error in force_reconcile does not kill the loop."""
    target_calls = 3
    call_count = 0
    # Signalled once the loop has driven force_reconcile through the two
    # error-raising calls AND a subsequent success. Waiting on this event
    # (instead of asserting a count after a fixed real sleep) makes the test
    # deterministic on slow/parallel CI runners.
    reached_target = asyncio.Event()

    async def flaky_force_reconcile(queue_name=None):
        nonlocal call_count
        call_count += 1
        if call_count >= target_calls:
            reached_target.set()
        if call_count < target_calls:
            msg = "transient"
            raise RuntimeError(msg)
        return {
            "recovered_orphans": 0,
            "recovered_crashed": 0,
            "failed_unrecoverable": 0,
        }

    service = MagicMock()
    service.force_reconcile = AsyncMock(side_effect=flaky_force_reconcile)

    shutdown = {"value": False}

    async def should_shutdown() -> bool:
        return shutdown["value"]

    loop_task = asyncio.create_task(
        _cortex_reconcile_safety_net_loop(
            service=service,
            # Near-zero interval: iterations are bounded by event delivery.
            interval_seconds=0,
            should_shutdown=should_shutdown,
        )
    )
    try:
        # Generous ceiling: it only bites if the loop dies on the first error.
        await asyncio.wait_for(reached_target.wait(), timeout=5.0)
    finally:
        shutdown["value"] = True
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task

    assert call_count >= target_calls
