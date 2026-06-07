# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Task 5.2: reconciler abandons tasks past started_at + timeout.

Verifies that reconcile_queue flags a task as abandoned via the absolute
started_at + timeout_seconds upper bound, independent of whether the
heartbeat key is still present.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.reconciler import reconcile_queue


_TIMEOUT_SECS = 3600  # mirrors settings.timeouts.llm_worker_default default


def _build_mock_client(
    *,
    running_members: list[str],
    task_hashes: dict[str, dict[str, str]],
    heartbeats: set[str],
) -> MagicMock:
    """Build a mock QueueClient with an in-memory Valkey substitute.

    Args:
        running_members: Task IDs currently in queue:{q}:running.
        task_hashes: task_id -> hash fields (empty dict means hash missing).
        heartbeats: Set of task_ids whose heartbeat key is live.
    """
    client = MagicMock()
    valkey = MagicMock()
    client.client = valkey

    valkey.smembers = AsyncMock(return_value={m.encode() for m in running_members})

    def _exists_side_effect(key: str) -> int:
        if isinstance(key, bytes):
            key = key.decode()
        if key.endswith(":heartbeat"):
            task_id = key.removeprefix("queue:task:").removesuffix(":heartbeat")
            return 1 if task_id in heartbeats else 0
        if key.startswith("queue:task:"):
            task_id = key.removeprefix("queue:task:")
            return 1 if task_hashes.get(task_id) else 0
        return 0

    valkey.exists = AsyncMock(side_effect=_exists_side_effect)

    def _hgetall_side_effect(key: str) -> dict[bytes, bytes]:
        if isinstance(key, bytes):
            key = key.decode()
        task_id = key.removeprefix("queue:task:")
        raw = task_hashes.get(task_id, {})
        return {k.encode(): v.encode() for k, v in raw.items()}

    valkey.hgetall = AsyncMock(side_effect=_hgetall_side_effect)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)
    valkey.zadd = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=1)
    # PERSIST/EXPIRE for the dead-letter retention path.
    valkey.persist = AsyncMock(return_value=True)
    valkey.expire = AsyncMock(return_value=True)

    client.get_retry_policy = MagicMock(return_value=False)
    # Proxy mark_task_failed_terminal through the mock so HSET + EXPIRE
    # land on the recorder existing tests already inspect.
    client._failed_result_ttl = 14 * 86_400

    async def _mark_failed(task_id: str, fields: dict[str, str]) -> None:
        await valkey.hset(f"queue:task:{task_id}", mapping=fields)
        await valkey.expire(f"queue:task:{task_id}", client._failed_result_ttl)

    client.mark_task_failed_terminal = AsyncMock(side_effect=_mark_failed)
    return client


@pytest.mark.asyncio
async def test_task_past_timeout_abandoned_even_with_live_heartbeat() -> None:
    """A task whose started_at is older than timeout_seconds must be flagged
    abandoned even if its heartbeat key is still present.

    This is the core invariant of Task 5.2: heartbeat liveness alone is not
    sufficient to declare a task healthy when started_at has exceeded the
    absolute upper bound. An event-loop hang can keep the heartbeat firing
    while the actual LLM call never completes.
    """
    stale_started_at = (datetime.now(UTC) - timedelta(seconds=_TIMEOUT_SECS + 60)).isoformat()

    client = _build_mock_client(
        running_members=["stale-task"],
        task_hashes={
            "stale-task": {
                "operation": "OP_EXTRACT_CHUNK",
                "attempts": "1",
                "priority": "50",
                "started_at": stale_started_at,
            }
        },
        heartbeats={"stale-task"},  # heartbeat is still alive
    )

    stats = await reconcile_queue(
        client,
        "llm",
        max_tries=5,
        timeout_seconds=_TIMEOUT_SECS,
    )

    # Should be classified abandoned (failed_unrecoverable or recovered_crashed,
    # depending on retry policy — policy returns False here so it's failed)
    assert stats.failed_unrecoverable == 1
    assert stats.recovered_crashed == 0
    client.client.srem.assert_any_await("queue:llm:running", "stale-task")


@pytest.mark.asyncio
async def test_task_within_timeout_with_live_heartbeat_is_healthy() -> None:
    """A task whose started_at is recent AND whose heartbeat is alive is healthy.

    No action should be taken.
    """
    fresh_started_at = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()

    client = _build_mock_client(
        running_members=["fresh-task"],
        task_hashes={
            "fresh-task": {
                "operation": "OP_EXTRACT_CHUNK",
                "attempts": "1",
                "priority": "50",
                "started_at": fresh_started_at,
            }
        },
        heartbeats={"fresh-task"},  # heartbeat alive
    )

    stats = await reconcile_queue(
        client,
        "llm",
        max_tries=5,
        timeout_seconds=_TIMEOUT_SECS,
    )

    assert stats.total() == 0
    client.client.srem.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_past_timeout_with_retry_policy_is_requeued() -> None:
    """A timed-out task with retry_on_crash=True and attempts < max_tries is requeued."""
    stale_started_at = (datetime.now(UTC) - timedelta(seconds=_TIMEOUT_SECS + 120)).isoformat()

    client = _build_mock_client(
        running_members=["retryable-stale"],
        task_hashes={
            "retryable-stale": {
                "operation": "OP_EXTRACT_CHUNK",
                "attempts": "2",
                "priority": "50",
                "started_at": stale_started_at,
            }
        },
        heartbeats={"retryable-stale"},  # heartbeat still alive
    )
    client.get_retry_policy = MagicMock(return_value=True)

    stats = await reconcile_queue(
        client,
        "llm",
        max_tries=5,
        timeout_seconds=_TIMEOUT_SECS,
    )

    assert stats.recovered_crashed == 1
    assert stats.failed_unrecoverable == 0
    client.client.zadd.assert_awaited()


@pytest.mark.asyncio
async def test_task_with_no_started_at_falls_through_to_heartbeat_check() -> None:
    """A task with no started_at in the hash uses the legacy heartbeat-only path.

    This covers tasks enqueued before Task 5.2 was deployed.
    """
    client = _build_mock_client(
        running_members=["legacy-task"],
        task_hashes={
            "legacy-task": {
                "operation": "OP_EXTRACT_CHUNK",
                "attempts": "1",
                "priority": "50",
                # No started_at field
            }
        },
        heartbeats={"legacy-task"},  # heartbeat alive — should be healthy
    )

    stats = await reconcile_queue(
        client,
        "llm",
        max_tries=5,
        timeout_seconds=_TIMEOUT_SECS,
    )

    # No started_at + live heartbeat → healthy
    assert stats.total() == 0
    client.client.srem.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_with_no_started_at_and_expired_heartbeat_is_abandoned() -> None:
    """Legacy task (no started_at) with expired heartbeat falls through to abandoned."""
    client = _build_mock_client(
        running_members=["legacy-abandoned"],
        task_hashes={
            "legacy-abandoned": {
                "operation": "OP_EXTRACT_CHUNK",
                "attempts": "1",
                "priority": "50",
            }
        },
        heartbeats=set(),  # heartbeat expired
    )

    stats = await reconcile_queue(
        client,
        "llm",
        max_tries=5,
        timeout_seconds=_TIMEOUT_SECS,
    )

    assert stats.failed_unrecoverable == 1
    client.client.srem.assert_any_await("queue:llm:running", "legacy-abandoned")


@pytest.mark.asyncio
async def test_timeout_none_disables_absolute_bound() -> None:
    """When timeout_seconds=None, the started_at check is skipped entirely.

    A stale task with a live heartbeat should be treated as healthy.
    """
    stale_started_at = (datetime.now(UTC) - timedelta(days=7)).isoformat()

    client = _build_mock_client(
        running_members=["ignored-stale"],
        task_hashes={
            "ignored-stale": {
                "operation": "OP_EXTRACT_CHUNK",
                "attempts": "1",
                "priority": "50",
                "started_at": stale_started_at,
            }
        },
        heartbeats={"ignored-stale"},  # heartbeat alive
    )

    stats = await reconcile_queue(
        client,
        "llm",
        max_tries=5,
        timeout_seconds=None,  # absolute bound disabled
    )

    assert stats.total() == 0
    client.client.srem.assert_not_awaited()
