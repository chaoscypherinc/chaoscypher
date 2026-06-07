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
    calls: list[str] = []

    async def fake_force_reconcile(queue_name=None):
        calls.append(queue_name or "all")
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
            interval_seconds=0.05,
            should_shutdown=should_shutdown,
        )
    )

    await asyncio.sleep(0.2)
    shutdown["value"] = True
    loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop_task

    # Should have invoked force_reconcile multiple times
    assert len(calls) >= 2
    assert all(c == "all" for c in calls)


@pytest.mark.asyncio
async def test_safety_net_survives_errors(
    structlog_for_caplog: None,  # pytest fixture, side-effect only
) -> None:
    """A transient error in force_reconcile does not kill the loop."""
    call_count = 0

    async def flaky_force_reconcile(queue_name=None):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
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
            interval_seconds=0.05,
            should_shutdown=should_shutdown,
        )
    )

    await asyncio.sleep(0.3)
    shutdown["value"] = True
    loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop_task

    assert call_count >= 3
