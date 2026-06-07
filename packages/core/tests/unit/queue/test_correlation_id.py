# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for correlation ID propagation across the queue boundary.

Covers:
- enqueue injects correlation_id from structlog contextvars when request_id
  is present and caller has not supplied an explicit correlation_id.
- enqueue with no contextvars works (no error, no correlation_id in metadata).
- Explicit metadata={"correlation_id": "..."} passed to enqueue takes
  precedence over the contextvars value (caller override wins).
- enqueue_tasks_batch propagates the same correlation_id to every task in
  the batch.
- _execute_handler binds correlation_id and request_id to structlog
  contextvars before calling the handler and unbinds after.
- _execute_handler does NOT bind when the task metadata carries no
  correlation_id.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.contextvars

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.client import QueueClient
from chaoscypher_core.queue.service import _execute_handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_recorder() -> tuple[MagicMock, list[dict[str, Any]]]:
    """Build a fake pipeline that records hset mapping dicts."""
    recorded: list[dict[str, Any]] = []

    pipeline = MagicMock()

    def _hset(key: str, mapping: dict[str, Any]) -> MagicMock:
        recorded.append({"key": key, "mapping": dict(mapping)})
        return pipeline

    pipeline.hset.side_effect = _hset
    pipeline.zadd.return_value = pipeline
    pipeline.lpush.return_value = pipeline
    pipeline.ltrim.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])

    return pipeline, recorded


def _make_queue_client() -> tuple[QueueClient, list[dict[str, Any]]]:
    """Construct a QueueClient wired to a recording pipeline."""
    client = QueueClient.__new__(QueueClient)
    client._connected = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 3600
    client._llm_result_ttl = 3600

    pipeline, recorded = _make_pipeline_recorder()

    valkey = MagicMock()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.pipeline = MagicMock(return_value=pipeline)

    client.client = valkey
    return client, recorded


def _get_enqueued_metadata(recorded: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the metadata JSON value from the first recorded hset call."""
    import json

    assert recorded, "No hset calls recorded — enqueue did not fire"
    return json.loads(recorded[0]["mapping"]["metadata"])


# ---------------------------------------------------------------------------
# enqueue — contextvars injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_injects_correlation_id_from_contextvars() -> None:
    """Enqueue propagates request_id from structlog contextvars as correlation_id."""
    client, recorded = _make_queue_client()

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-abc-123")
    try:
        await client.enqueue(
            queue=QUEUE_OPERATIONS,
            operation="test_op",
            data={"x": 1},
        )
    finally:
        structlog.contextvars.clear_contextvars()

    metadata = _get_enqueued_metadata(recorded)
    assert metadata["correlation_id"] == "req-abc-123"


@pytest.mark.asyncio
async def test_enqueue_no_contextvars_no_correlation_id() -> None:
    """Enqueue works without error and omits correlation_id when contextvars is empty."""
    client, recorded = _make_queue_client()

    structlog.contextvars.clear_contextvars()
    await client.enqueue(
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={"x": 1},
    )

    metadata = _get_enqueued_metadata(recorded)
    assert "correlation_id" not in metadata


@pytest.mark.asyncio
async def test_enqueue_explicit_correlation_id_wins_over_contextvars() -> None:
    """Caller-supplied correlation_id takes precedence over the contextvars value."""
    client, recorded = _make_queue_client()

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-from-context")
    try:
        await client.enqueue(
            queue=QUEUE_OPERATIONS,
            operation="test_op",
            data={"x": 1},
            metadata={"correlation_id": "explicit-override"},
        )
    finally:
        structlog.contextvars.clear_contextvars()

    metadata = _get_enqueued_metadata(recorded)
    assert metadata["correlation_id"] == "explicit-override"


# ---------------------------------------------------------------------------
# enqueue_tasks_batch — contextvars injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_tasks_batch_injects_correlation_id_into_every_task() -> None:
    """Batch enqueue stamps every task's metadata with the correlation_id."""
    import json

    client, recorded = _make_queue_client()

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-batch-999")
    try:
        await client.enqueue_tasks_batch(
            queue=QUEUE_OPERATIONS,
            tasks=[
                {"operation": "op1", "data": {}, "priority": 50, "metadata": {}},
                {"operation": "op2", "data": {}, "priority": 50, "metadata": {}},
            ],
        )
    finally:
        structlog.contextvars.clear_contextvars()

    assert len(recorded) == 2
    for entry in recorded:
        m = json.loads(entry["mapping"]["metadata"])
        assert m.get("correlation_id") == "req-batch-999", (
            f"Expected correlation_id in {entry['mapping']['metadata']!r}"
        )


# ---------------------------------------------------------------------------
# _execute_handler — worker-side contextvars binding
# ---------------------------------------------------------------------------


def _make_fake_valkey_client() -> MagicMock:
    """Build a minimal fake Valkey client for _execute_handler."""
    client = MagicMock()
    client.hset = AsyncMock(return_value=1)
    client.setex = AsyncMock(return_value=True)
    return client


@pytest.mark.asyncio
async def test_execute_handler_binds_correlation_id_during_handler() -> None:
    """_execute_handler binds correlation_id AND request_id while the handler runs."""
    observed: dict[str, Any] = {}

    async def capturing_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> str:
        observed.update(structlog.contextvars.get_contextvars())
        return "done"

    valkey = _make_fake_valkey_client()
    structlog.contextvars.clear_contextvars()

    await _execute_handler(
        handler=capturing_handler,
        task_id="t-001",
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={},
        metadata={"correlation_id": "corr-xyz"},
        result_ttl=60,
        client=valkey,
    )

    assert observed.get("correlation_id") == "corr-xyz"
    assert observed.get("request_id") == "corr-xyz"


@pytest.mark.asyncio
async def test_execute_handler_unbinds_correlation_id_after_handler() -> None:
    """_execute_handler clears correlation_id and request_id after the handler returns."""

    async def noop_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> str:
        return "ok"

    valkey = _make_fake_valkey_client()
    structlog.contextvars.clear_contextvars()

    await _execute_handler(
        handler=noop_handler,
        task_id="t-002",
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={},
        metadata={"correlation_id": "corr-abc"},
        result_ttl=60,
        client=valkey,
    )

    ctx = structlog.contextvars.get_contextvars()
    assert "correlation_id" not in ctx
    assert "request_id" not in ctx


@pytest.mark.asyncio
async def test_execute_handler_no_correlation_id_no_binding() -> None:
    """_execute_handler does not bind correlation_id when metadata has none."""

    async def noop_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> str:
        return "ok"

    valkey = _make_fake_valkey_client()
    structlog.contextvars.clear_contextvars()

    await _execute_handler(
        handler=noop_handler,
        task_id="t-003",
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={},
        metadata={},
        result_ttl=60,
        client=valkey,
    )

    ctx = structlog.contextvars.get_contextvars()
    assert "correlation_id" not in ctx
    assert "request_id" not in ctx


@pytest.mark.asyncio
async def test_execute_handler_unbinds_correlation_id_after_exception() -> None:
    """_execute_handler unbinds correlation_id even when the handler raises permanently."""

    async def failing_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> str:
        msg = "boom"
        raise ValueError(msg)

    valkey = _make_fake_valkey_client()
    structlog.contextvars.clear_contextvars()

    # Permanent error — _execute_handler returns dict, does not re-raise
    result = await _execute_handler(
        handler=failing_handler,
        task_id="t-004",
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={},
        metadata={"correlation_id": "corr-fail"},
        result_ttl=60,
        client=valkey,
    )

    assert result["status"] == "failed"

    ctx = structlog.contextvars.get_contextvars()
    assert "correlation_id" not in ctx
    assert "request_id" not in ctx
