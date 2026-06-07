# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the periodic _source_recovery_loop in worker.py.

Pinned behaviour:
- Calls recovery.reconcile_database in a loop at the configured interval.
- A TimeoutError from the wrapped reconcile call is caught + logged, loop continues.
- Cancellation stops the loop cleanly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import chaoscypher_neuron.worker  # noqa: F401
from chaoscypher_neuron.worker import _source_recovery_loop


@pytest.mark.asyncio
async def test_source_recovery_loop_calls_reconcile_repeatedly() -> None:
    """The loop fires reconcile_database repeatedly until cancelled."""
    recovery = MagicMock()
    recovery.reconcile_database = AsyncMock(
        return_value=MagicMock(recovered=0, skipped_paused=0),
    )

    task = asyncio.create_task(
        _source_recovery_loop(
            recovery=recovery,
            adapter=MagicMock(),
            database_name="test_db",
            interval_seconds=0.02,  # type: ignore[arg-type]
            reconcile_timeout_seconds=10,
        )
    )

    # Let the loop run ~3 iterations.
    await asyncio.sleep(0.08)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert recovery.reconcile_database.call_count >= 2, (
        f"Expected >=2 reconcile calls in 0.08s @ 0.02s interval; "
        f"got {recovery.reconcile_database.call_count}"
    )
    for call in recovery.reconcile_database.call_args_list:
        assert call.kwargs.get("database_name") == "test_db"


@pytest.mark.asyncio
async def test_source_recovery_loop_continues_on_timeout() -> None:
    """A TimeoutError inside the wrapped reconcile is caught + the loop continues."""
    recovery = MagicMock()
    call_count = 0

    async def slow_or_normal(database_name: str) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: take longer than the timeout to trigger TimeoutError.
            await asyncio.sleep(0.2)
        return MagicMock(recovered=0, skipped_paused=0)

    recovery.reconcile_database = AsyncMock(side_effect=slow_or_normal)

    task = asyncio.create_task(
        _source_recovery_loop(
            recovery=recovery,
            adapter=MagicMock(),
            database_name="test_db",
            interval_seconds=0.01,  # type: ignore[arg-type]
            reconcile_timeout_seconds=0.05,  # type: ignore[arg-type]
        )
    )

    # Wait long enough for the timeout to fire + subsequent successful iterations.
    await asyncio.sleep(1.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # We expect at least 2 calls (one that timed out, one or more that succeeded).
    assert call_count >= 2, f"Loop did not continue after timeout; got {call_count} calls"


@pytest.mark.asyncio
async def test_source_recovery_loop_stops_on_cancel() -> None:
    """Cancellation stops the loop cleanly (no more reconcile calls after cancel)."""
    recovery = MagicMock()
    recovery.reconcile_database = AsyncMock(
        return_value=MagicMock(recovered=0, skipped_paused=0),
    )

    task = asyncio.create_task(
        _source_recovery_loop(
            recovery=recovery,
            adapter=MagicMock(),
            database_name="test_db",
            interval_seconds=0.02,  # type: ignore[arg-type]
            reconcile_timeout_seconds=10,
        )
    )

    await asyncio.sleep(0.05)
    pre_cancel = recovery.reconcile_database.call_count
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Wait for any in-flight call to settle.
    await asyncio.sleep(0.1)
    post_wait = recovery.reconcile_database.call_count
    assert post_wait <= pre_cancel + 1, (
        f"Cancel did not stop the loop; pre={pre_cancel}, post-wait={post_wait}"
    )
