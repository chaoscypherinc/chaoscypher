# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sanity tests for the WorkerHarness fixture itself."""

from __future__ import annotations

import sys
from pathlib import Path

# Import for side-effect — worker.py's configure_logging() at import time
# needs to settle before any fixture activates.
import chaoscypher_neuron.worker  # noqa: F401


# worker_harness.py lives in packages/neuron/tests/fixtures/ which is added to
# sys.path by conftest.py at runtime.
sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
from worker_harness import WorkerHarness  # type: ignore[import-not-found]


def test_worker_harness_ctx_is_populated(worker_harness: WorkerHarness) -> None:
    """The stub WorkerContext has every key the setup helpers read."""
    ctx = worker_harness.ctx
    assert ctx["settings"] is not None
    assert ctx["current_database"] == "test_db"
    assert ctx["llm_service"] is not None
    assert ctx["graph_repository"] is not None
    assert ctx["search_repository"] is not None
    assert ctx["storage_adapter"] is not None
    assert ctx["config_manager"] is not None


def test_recording_queue_client_starts_empty(worker_harness: WorkerHarness) -> None:
    """A fresh harness has no registered handlers."""
    assert worker_harness.queue.all_registered_ops() == set()
    assert worker_harness.queue.registered_on("llm") == {}
    assert worker_harness.queue.registered_on("operations") == {}


def test_recording_queue_client_captures_register_handlers(worker_harness: WorkerHarness) -> None:
    """register_handlers calls are observable via registered_on."""

    async def _fake_handler(data: dict) -> dict:
        return {}

    worker_harness.queue.register_handlers("operations", {"some_op": _fake_handler})
    assert worker_harness.queue.registered_on("operations") == {"some_op": _fake_handler}
    assert "some_op" in worker_harness.queue.all_registered_ops()


def test_recording_queue_client_merges_multiple_registers(worker_harness: WorkerHarness) -> None:
    """Multiple register_handlers calls for the same queue merge."""

    async def _h1(data: dict) -> dict:
        return {}

    async def _h2(data: dict) -> dict:
        return {}

    worker_harness.queue.register_handlers("operations", {"op1": _h1})
    worker_harness.queue.register_handlers("operations", {"op2": _h2})
    registered = worker_harness.queue.registered_on("operations")
    assert registered == {"op1": _h1, "op2": _h2}


import pytest


@pytest.mark.asyncio
async def test_recording_queue_dispatches_to_registered_handler(
    worker_harness: WorkerHarness,
) -> None:
    """dispatch(queue, op, data) invokes the registered handler with the payload."""
    received: dict[str, object] = {}

    async def my_handler(
        data: dict, metadata: dict | None = None, task_id: str | None = None
    ) -> dict:
        received["data"] = data
        received["metadata"] = metadata
        received["task_id"] = task_id
        return {"ok": True}

    worker_harness.queue.register_handlers("operations", {"my_op": my_handler})
    result = await worker_harness.queue.dispatch(
        "operations",
        "my_op",
        {"key": "value"},
        metadata={"m": 1},
        task_id="t-123",
    )

    assert received == {"data": {"key": "value"}, "metadata": {"m": 1}, "task_id": "t-123"}
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_recording_queue_dispatch_raises_when_op_unregistered(
    worker_harness: WorkerHarness,
) -> None:
    """Dispatch on an unregistered op raises KeyError with the op name."""
    with pytest.raises(KeyError, match="nonexistent_op"):
        await worker_harness.queue.dispatch("operations", "nonexistent_op", {})
