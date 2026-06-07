# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that the QueueWorker's _process_task wires correlation_id into
structlog contextvars (via _execute_handler) before invoking the handler.

This exercises the full worker-side path: popped task hash → _process_task
→ _execute_handler → handler called with correlation_id bound.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.contextvars

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.worker import QueueWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_worker_with_hash(
    handler: Any,
    hash_payload: dict[str, str],
) -> tuple[QueueWorker, MagicMock]:
    """Return a QueueWorker + fake Valkey client backed by a fixed task hash."""
    valkey = MagicMock()
    valkey.sadd = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hgetall = AsyncMock(return_value=hash_payload)
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)
    valkey.setex = AsyncMock(return_value=True)

    worker = QueueWorker(
        client=valkey,
        queues_config={QUEUE_OPERATIONS: {"concurrency": 1, "max_tries": 3, "timeout": 60}},
        handlers={QUEUE_OPERATIONS: {"test_op": handler}},
    )
    return worker, valkey


def _make_hash(correlation_id: str | None = None) -> dict[str, str]:
    """Build a minimal task hash; optionally includes correlation_id in metadata."""
    meta: dict[str, Any] = {}
    if correlation_id is not None:
        meta["correlation_id"] = correlation_id
    return {
        "task_id": "t-worker-test",
        "queue": QUEUE_OPERATIONS,
        "operation": "test_op",
        "status": "queued",
        "priority": "50",
        "created_at": "2026-05-22T00:00:00Z",
        "data": json.dumps({}),
        "metadata": json.dumps(meta),
        "result_ttl": "3600",
        "attempts": "0",
        "payload_version": "1",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_binds_correlation_id_during_handler() -> None:
    """_process_task binds correlation_id + request_id to structlog contextvars
    for the duration of handler execution when the task metadata carries one.
    """
    observed: dict[str, Any] = {}

    async def capturing_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> str:
        observed.update(structlog.contextvars.get_contextvars())
        return "ok"

    worker, _valkey = _build_worker_with_hash(capturing_handler, _make_hash("corr-worker-123"))

    structlog.contextvars.clear_contextvars()

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-worker-test",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    assert observed.get("correlation_id") == "corr-worker-123"
    assert observed.get("request_id") == "corr-worker-123"


@pytest.mark.asyncio
async def test_process_task_unbinds_correlation_id_after_handler() -> None:
    """_process_task clears correlation_id / request_id from contextvars after
    the handler finishes so subsequent tasks see a clean slate.
    """

    async def noop_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> str:
        return "done"

    worker, _valkey = _build_worker_with_hash(noop_handler, _make_hash("corr-cleanup-999"))

    structlog.contextvars.clear_contextvars()

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-worker-test",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    ctx = structlog.contextvars.get_contextvars()
    assert "correlation_id" not in ctx
    assert "request_id" not in ctx


@pytest.mark.asyncio
async def test_process_task_no_correlation_id_no_binding() -> None:
    """_process_task does not bind correlation_id when task metadata has none."""

    async def noop_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> str:
        return "done"

    # No correlation_id in metadata
    worker, _valkey = _build_worker_with_hash(noop_handler, _make_hash(correlation_id=None))

    structlog.contextvars.clear_contextvars()

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-worker-test",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    ctx = structlog.contextvars.get_contextvars()
    assert "correlation_id" not in ctx
    assert "request_id" not in ctx
