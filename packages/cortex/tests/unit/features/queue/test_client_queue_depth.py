# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for queue depth backpressure on QueueClient."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import QueueFullError
from chaoscypher_core.queue.client import QueueClient


def _build_client(max_depth: int = 100) -> tuple[QueueClient, MagicMock]:
    """Construct a QueueClient with a mocked Valkey connection.

    Args:
        max_depth: Maximum pending queue depth to configure.

    Returns:
        Tuple of (QueueClient, mock Valkey instance).

    """
    client = QueueClient()
    valkey = MagicMock()
    client.client = valkey
    client._connected = True
    client._max_pending_queue_depth = max_depth
    return client, valkey


# ============================================================================
# _check_queue_depth
# ============================================================================


@pytest.mark.asyncio
async def test_check_queue_depth_passes_when_under_limit() -> None:
    """No error raised when current depth is below the limit."""
    client, valkey = _build_client(max_depth=100)
    valkey.zcard = AsyncMock(return_value=50)

    # Should not raise
    await client._check_queue_depth("llm")

    valkey.zcard.assert_awaited_once_with("queue:llm:pending")


@pytest.mark.asyncio
async def test_check_queue_depth_raises_when_at_limit() -> None:
    """QueueFullError raised when current depth equals the limit."""
    client, valkey = _build_client(max_depth=100)
    valkey.zcard = AsyncMock(return_value=100)

    with pytest.raises(QueueFullError) as exc_info:
        await client._check_queue_depth("llm")

    assert exc_info.value.queue == "llm"
    assert exc_info.value.current_depth == 100
    assert exc_info.value.max_depth == 100


@pytest.mark.asyncio
async def test_check_queue_depth_raises_when_over_limit() -> None:
    """QueueFullError raised when current depth exceeds the limit."""
    client, valkey = _build_client(max_depth=100)
    valkey.zcard = AsyncMock(return_value=150)

    with pytest.raises(QueueFullError) as exc_info:
        await client._check_queue_depth("operations")

    assert exc_info.value.queue == "operations"
    assert exc_info.value.current_depth == 150


# ============================================================================
# enqueue() with depth check
# ============================================================================


@pytest.mark.asyncio
async def test_enqueue_raises_queue_full_when_at_limit() -> None:
    """enqueue() raises QueueFullError before adding to the sorted set."""
    client, valkey = _build_client(max_depth=5)
    valkey.zcard = AsyncMock(return_value=5)

    with pytest.raises(QueueFullError):
        await client.enqueue(
            queue="llm",
            operation="chat_completion",
            data={"messages": []},
        )

    # Pipeline should never have been created since we rejected early
    valkey.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_succeeds_when_under_limit() -> None:
    """enqueue() proceeds normally when the queue has capacity."""
    client, valkey = _build_client(max_depth=100)
    valkey.zcard = AsyncMock(return_value=10)

    mock_pipeline = MagicMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[])
    valkey.pipeline = MagicMock(return_value=mock_pipeline)

    task_id = await client.enqueue(
        queue="operations",
        operation="process_source",
        data={"source_id": "test"},
    )

    assert isinstance(task_id, str)
    assert len(task_id) > 0
    mock_pipeline.execute.assert_awaited_once()


# ============================================================================
# enqueue_tasks_batch() with depth check
# ============================================================================


@pytest.mark.asyncio
async def test_enqueue_batch_raises_when_batch_would_exceed_limit() -> None:
    """enqueue_tasks_batch() rejects when current + batch > max."""
    client, valkey = _build_client(max_depth=10)
    valkey.zcard = AsyncMock(return_value=8)

    tasks = [
        {"operation": "op", "data": {}, "priority": 50, "metadata": {}}
        for _ in range(5)  # 8 + 5 = 13 > 10
    ]

    with pytest.raises(QueueFullError) as exc_info:
        await client.enqueue_tasks_batch(queue="operations", tasks=tasks)

    assert exc_info.value.current_depth == 8
    assert exc_info.value.max_depth == 10


@pytest.mark.asyncio
async def test_enqueue_batch_succeeds_when_within_limit() -> None:
    """enqueue_tasks_batch() proceeds when current + batch <= max."""
    client, valkey = _build_client(max_depth=10)
    valkey.zcard = AsyncMock(return_value=5)

    mock_pipeline = MagicMock()
    mock_pipeline.hset = MagicMock()
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[])
    valkey.pipeline = MagicMock(return_value=mock_pipeline)

    tasks = [
        {"operation": "op", "data": {}, "priority": 50, "metadata": {}}
        for _ in range(3)  # 5 + 3 = 8 <= 10
    ]

    task_ids = await client.enqueue_tasks_batch(queue="operations", tasks=tasks)

    assert len(task_ids) == 3
    mock_pipeline.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_batch_empty_skips_depth_check() -> None:
    """enqueue_tasks_batch() returns early for empty task list (no ZCARD call)."""
    client, valkey = _build_client(max_depth=0)  # Even with max=0, empty is fine
    valkey.zcard = AsyncMock(return_value=0)

    result = await client.enqueue_tasks_batch(queue="llm", tasks=[])

    assert result == []
    valkey.zcard.assert_not_called()


# ============================================================================
# QueueFullError exception shape
# ============================================================================


def test_queue_full_error_attributes() -> None:
    """QueueFullError carries the expected attributes and code."""
    err = QueueFullError(queue="llm", current_depth=500, max_depth=500)

    assert err.code == "QUEUE_FULL"
    assert err.queue == "llm"
    assert err.current_depth == 500
    assert err.max_depth == 500
    assert "llm" in err.message
    assert "500" in err.message


def test_queue_full_error_details_dict() -> None:
    """QueueFullError details dict contains queue, current_depth, max_depth."""
    err = QueueFullError(queue="operations", current_depth=100, max_depth=200)

    assert err.details["queue"] == "operations"
    assert err.details["current_depth"] == 100
    assert err.details["max_depth"] == 200


# ============================================================================
# Config: max_pending_queue_depth stored during connect()
# ============================================================================


def test_default_max_pending_queue_depth() -> None:
    """QueueClient defaults to 10000 for max pending queue depth."""
    client = QueueClient()
    assert client._max_pending_queue_depth == 10000
