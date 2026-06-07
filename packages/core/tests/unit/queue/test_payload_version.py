# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for queue task envelope payload versioning.

Covers:
- enqueue (single + batch) stamps every task with `payload_version`.
- `_decode_record` surfaces `payload_version` and defaults legacy hashes to 1.
- Worker `_process_task` refuses dispatch for unsupported versions and marks
  the task `failed` with `error_type=permanent`.
- Worker treats missing `payload_version` as v1 (transitional window).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.client import (
    CURRENT_PAYLOAD_VERSION,
    SUPPORTED_PAYLOAD_VERSIONS,
    QueueClient,
)


def _make_pipeline_recorder() -> tuple[MagicMock, list[dict[str, Any]]]:
    """Build a fake pipeline that records hset mapping dicts.

    Returns the pipeline mock and the list it appends to. The pipeline
    is chainable (returns itself) for hset/zadd/lpush/ltrim and exposes
    an awaitable execute() so the production code can pipeline-then-execute.
    """
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


def _make_queue_client_with_pipeline() -> tuple[QueueClient, list[dict[str, Any]]]:
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


# ---------------------------------------------------------------------------
# Envelope contract
# ---------------------------------------------------------------------------


def test_current_payload_version_is_in_supported_set() -> None:
    """CURRENT_PAYLOAD_VERSION must always be in SUPPORTED_PAYLOAD_VERSIONS."""
    assert CURRENT_PAYLOAD_VERSION in SUPPORTED_PAYLOAD_VERSIONS


@pytest.mark.asyncio
async def test_enqueue_stamps_payload_version() -> None:
    """Single enqueue writes the current payload_version to the hash."""
    client, recorded = _make_queue_client_with_pipeline()

    await client.enqueue(
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={"foo": "bar"},
    )

    assert len(recorded) == 1
    mapping = recorded[0]["mapping"]
    assert mapping["payload_version"] == str(CURRENT_PAYLOAD_VERSION)
    assert recorded[0]["key"].startswith("queue:task:")


@pytest.mark.asyncio
async def test_enqueue_batch_stamps_payload_version() -> None:
    """Batch enqueue stamps every task with the current payload_version."""
    client, recorded = _make_queue_client_with_pipeline()

    await client.enqueue_tasks_batch(
        queue=QUEUE_OPERATIONS,
        tasks=[
            {"operation": "op1", "data": {}, "priority": 50, "metadata": {}},
            {"operation": "op2", "data": {}, "priority": 50, "metadata": {}},
        ],
    )

    assert len(recorded) == 2
    for entry in recorded:
        assert entry["mapping"]["payload_version"] == str(CURRENT_PAYLOAD_VERSION)


def test_decode_record_surfaces_payload_version() -> None:
    """_decode_record exposes payload_version as int when present."""
    client = QueueClient.__new__(QueueClient)
    decoded = client._decode_record(
        {
            "task_id": "t1",
            "queue": QUEUE_OPERATIONS,
            "operation": "test_op",
            "status": "queued",
            "priority": "50",
            "created_at": "2026-05-19T00:00:00Z",
            "metadata": "{}",
            "data": "{}",
            "attempts": "0",
            "payload_version": "1",
        }
    )
    assert decoded["payload_version"] == 1


def test_decode_record_defaults_missing_payload_version_to_one() -> None:
    """Legacy hashes without payload_version decode to version 1 (transitional)."""
    client = QueueClient.__new__(QueueClient)
    decoded = client._decode_record(
        {
            "task_id": "t1",
            "queue": QUEUE_OPERATIONS,
            "operation": "test_op",
            "status": "queued",
            "priority": "50",
            "created_at": "2026-05-19T00:00:00Z",
            "metadata": "{}",
            "data": "{}",
            "attempts": "0",
        }
    )
    assert decoded["payload_version"] == 1


# ---------------------------------------------------------------------------
# Worker dispatch gate
# ---------------------------------------------------------------------------


def _build_worker(
    handler: Any,
    hash_payload: dict[str, str],
) -> tuple[Any, MagicMock]:
    """Construct a QueueWorker with mocked valkey returning the given hash."""
    from chaoscypher_core.queue.worker import QueueWorker

    valkey = MagicMock()
    valkey.sadd = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hgetall = AsyncMock(return_value=hash_payload)
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)

    worker = QueueWorker(
        client=valkey,
        queues_config={QUEUE_OPERATIONS: {"concurrency": 1, "max_tries": 3, "timeout": 60}},
        handlers={QUEUE_OPERATIONS: {"test_op": handler}},
    )
    return worker, valkey


@pytest.mark.asyncio
async def test_worker_rejects_unsupported_payload_version() -> None:
    """Worker marks unsupported payload versions as failed (permanent), no retry."""
    handler_called = False

    async def fake_handler(*args: Any, **kwargs: Any) -> Any:
        nonlocal handler_called
        handler_called = True
        return None

    hash_payload = {
        "task_id": "test-unsupported",
        "queue": QUEUE_OPERATIONS,
        "operation": "test_op",
        "status": "queued",
        "priority": "50",
        "created_at": "2026-05-19T00:00:00Z",
        "data": json.dumps({}),
        "metadata": json.dumps({}),
        "result_ttl": "3600",
        "attempts": "0",
        "payload_version": "99",  # future / unsupported
    }
    worker, valkey = _build_worker(fake_handler, hash_payload)

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "test-unsupported",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    assert not handler_called

    # Find the hset call that marked the task failed.
    failing_calls = [
        call
        for call in valkey.hset.call_args_list
        if "status" in call.kwargs.get("mapping", {})
        and call.kwargs["mapping"]["status"] == "failed"
    ]
    assert failing_calls, "Worker did not mark task as failed"
    mapping = failing_calls[0].kwargs["mapping"]
    assert mapping["error_type"] == "permanent"
    assert "payload_version" in mapping["error"]


@pytest.mark.asyncio
async def test_worker_accepts_legacy_missing_payload_version() -> None:
    """Worker treats missing payload_version as v1 (transitional window)."""
    handler_called = False
    received_data: dict[str, Any] = {}

    async def fake_handler(
        data: dict[str, Any],
        *,
        metadata: dict[str, Any],
        task_id: str,
    ) -> Any:
        nonlocal handler_called, received_data
        handler_called = True
        received_data = data
        return "ok"

    # No payload_version field — simulates a v0 task already queued at upgrade time.
    hash_payload = {
        "task_id": "test-legacy",
        "queue": QUEUE_OPERATIONS,
        "operation": "test_op",
        "status": "queued",
        "priority": "50",
        "created_at": "2026-05-19T00:00:00Z",
        "data": json.dumps({"foo": "bar"}),
        "metadata": json.dumps({}),
        "result_ttl": "3600",
        "attempts": "0",
    }
    worker, _valkey = _build_worker(fake_handler, hash_payload)

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "test-legacy",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    assert handler_called
    assert received_data == {"foo": "bar"}
