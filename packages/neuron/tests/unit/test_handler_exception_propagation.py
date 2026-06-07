# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for handler-exception propagation through dispatch."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

import chaoscypher_neuron.worker  # noqa: F401


sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
from worker_harness import WorkerHarness  # type: ignore[import-not-found]


@pytest.mark.asyncio
async def test_dispatch_propagates_runtime_error(worker_harness: WorkerHarness) -> None:
    """A handler raising RuntimeError causes dispatch to re-raise unchanged."""

    async def failing_handler(
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> None:
        raise RuntimeError("simulated handler failure")

    worker_harness.queue.register_handlers("operations", {"my_op": failing_handler})
    with pytest.raises(RuntimeError, match="simulated handler failure"):
        await worker_harness.queue.dispatch("operations", "my_op", {})


@pytest.mark.asyncio
async def test_dispatch_propagates_chaoscypher_exception(
    worker_harness: WorkerHarness,
) -> None:
    """A handler raising a ChaosCypherException subclass propagates with full context."""
    from chaoscypher_core.exceptions import ChaosCypherException

    class CustomChaosError(ChaosCypherException):
        """Test-only subclass."""

    async def failing_handler(
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> None:
        raise CustomChaosError("simulated domain error")

    worker_harness.queue.register_handlers("operations", {"my_op": failing_handler})
    with pytest.raises(CustomChaosError, match="simulated domain error"):
        await worker_harness.queue.dispatch("operations", "my_op", {})


@pytest.mark.asyncio
async def test_dispatch_propagates_cancellation(worker_harness: WorkerHarness) -> None:
    """A handler that gets cancelled propagates CancelledError, not RuntimeError."""

    async def slow_handler(
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> None:
        await asyncio.sleep(10)

    worker_harness.queue.register_handlers("operations", {"my_op": slow_handler})

    dispatch_task = asyncio.create_task(worker_harness.queue.dispatch("operations", "my_op", {}))
    await asyncio.sleep(0.01)
    dispatch_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dispatch_task
