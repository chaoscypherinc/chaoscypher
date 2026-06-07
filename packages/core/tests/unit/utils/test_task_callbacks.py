# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify background tasks surface exceptions via add_done_callback."""

from __future__ import annotations

import asyncio

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.utils.task_callbacks import log_task_exception


@pytest.mark.asyncio
async def test_log_task_exception_logs_when_task_raises() -> None:
    """A failing background task surfaces the exception via the callback."""

    async def failing_coro() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    task = asyncio.create_task(failing_coro())
    task.add_done_callback(log_task_exception)
    with capture_logs() as captured:
        try:
            await task
        except RuntimeError:
            pass
        await asyncio.sleep(0)

    events = [c.get("event") for c in captured]
    assert "background_task_failed" in events


@pytest.mark.asyncio
async def test_log_task_exception_quiet_on_cancellation() -> None:
    """Normal cancellation does not log as a failure."""

    async def long_coro() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(long_coro())
    task.add_done_callback(log_task_exception)
    task.cancel()
    with capture_logs() as captured:
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)

    events = [c.get("event") for c in captured]
    assert "background_task_failed" not in events
