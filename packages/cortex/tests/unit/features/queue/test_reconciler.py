# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for reconcile_queue classification logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.reconciler import reconcile_queue


def _build_mock_client(
    *,
    running_members: list[str],
    task_hashes: dict[str, dict[str, str]],
    heartbeats: set[str],
) -> MagicMock:
    """Build a mock QueueClient with an in-memory Valkey substitute.

    - running_members: list of task IDs currently in queue:{q}:running
    - task_hashes: task_id -> hash fields (empty dict means hash missing)
    - heartbeats: set of task_ids whose heartbeat key is live
    """
    client = MagicMock()
    valkey = MagicMock()
    client.client = valkey

    valkey.smembers = AsyncMock(return_value={m.encode() for m in running_members})

    def _exists_side_effect(key):
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

    def _hgetall_side_effect(key):
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
    # PERSIST/EXPIRE for the dead-letter retention path: reconciler clears
    # any stray TTL on requeue and applies the failed-result TTL on
    # terminal abandonment via ``QueueClient.mark_task_failed_terminal``.
    valkey.persist = AsyncMock(return_value=True)
    valkey.expire = AsyncMock(return_value=True)

    client.get_retry_policy = MagicMock(return_value=False)
    # ``QueueClient.mark_task_failed_terminal`` is what the reconciler calls
    # on terminal abandonment; route it through the mock valkey so HSET +
    # EXPIRE land on the same recorder the tests already inspect.
    client._failed_result_ttl = 14 * 86_400

    async def _mark_failed(task_id: str, fields: dict[str, str]) -> None:
        await valkey.hset(f"queue:task:{task_id}", mapping=fields)
        await valkey.expire(f"queue:task:{task_id}", client._failed_result_ttl)

    client.mark_task_failed_terminal = AsyncMock(side_effect=_mark_failed)
    return client


@pytest.mark.asyncio
async def test_orphan_id_is_removed_from_running_set() -> None:
    """Task ID in running set with no hash and no heartbeat is an orphan.

    Reproduces the production bug: task 3227653c-... in queue:llm:running
    with its hash absent.
    """
    client = _build_mock_client(
        running_members=["3227653c-ad8b-4570-aab0-2b8341addd91"],
        task_hashes={},  # hash does not exist
        heartbeats=set(),  # heartbeat does not exist
    )

    stats = await reconcile_queue(client, "llm", max_tries=5)

    assert stats.recovered_orphans == 1
    assert stats.recovered_crashed == 0
    assert stats.failed_unrecoverable == 0
    client.client.srem.assert_any_await("queue:llm:running", "3227653c-ad8b-4570-aab0-2b8341addd91")


@pytest.mark.asyncio
async def test_empty_running_set_returns_zero_stats() -> None:
    """No task IDs in the running set is a no-op with zero counters."""
    client = _build_mock_client(
        running_members=[],
        task_hashes={},
        heartbeats=set(),
    )

    stats = await reconcile_queue(client, "llm", max_tries=5)

    assert stats.total() == 0
    client.client.srem.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_orphans_all_removed() -> None:
    """Reconciler processes every ID in the running set, not just one."""
    client = _build_mock_client(
        running_members=["orphan-1", "orphan-2", "orphan-3"],
        task_hashes={},
        heartbeats=set(),
    )

    stats = await reconcile_queue(client, "operations", max_tries=5)

    assert stats.recovered_orphans == 3
    assert client.client.srem.await_count == 3


@pytest.mark.asyncio
async def test_healthy_task_is_skipped() -> None:
    """Task with both hash and heartbeat is healthy — no action."""
    client = _build_mock_client(
        running_members=["healthy-id"],
        task_hashes={"healthy-id": {"operation": "op_a", "attempts": "1", "priority": "50"}},
        heartbeats={"healthy-id"},
    )

    stats = await reconcile_queue(client, "llm", max_tries=5)

    assert stats.total() == 0
    client.client.srem.assert_not_awaited()


@pytest.mark.asyncio
async def test_abandoned_task_retries_when_policy_allows() -> None:
    """retry_on_crash=True + attempts < max_tries -> requeue."""
    client = _build_mock_client(
        running_members=["abandoned-id"],
        task_hashes={
            "abandoned-id": {
                "operation": "OP_INDEX_DOCUMENT",
                "attempts": "2",
                "priority": "50",
            }
        },
        heartbeats=set(),  # heartbeat expired
    )
    client.get_retry_policy = MagicMock(return_value=True)

    stats = await reconcile_queue(client, "operations", max_tries=5)

    assert stats.recovered_crashed == 1
    assert stats.failed_unrecoverable == 0
    client.client.srem.assert_any_await("queue:operations:running", "abandoned-id")
    # Should be re-added to pending
    client.client.zadd.assert_awaited()
    client.client.hset.assert_awaited()


@pytest.mark.asyncio
async def test_abandoned_task_fails_when_policy_denies() -> None:
    """retry_on_crash=False -> mark failed, do not requeue."""
    client = _build_mock_client(
        running_members=["abandoned-id"],
        task_hashes={
            "abandoned-id": {
                "operation": "chat_background",
                "attempts": "1",
                "priority": "10",
            }
        },
        heartbeats=set(),
    )
    client.get_retry_policy = MagicMock(return_value=False)

    stats = await reconcile_queue(client, "llm", max_tries=5)

    assert stats.failed_unrecoverable == 1
    assert stats.recovered_crashed == 0
    client.client.zadd.assert_not_awaited()
    # Task hash should be marked failed
    hset_calls = client.client.hset.await_args_list
    assert any("failed" in str(c) for c in hset_calls), (
        f"Expected hset call marking task as failed, got {hset_calls}"
    )


@pytest.mark.asyncio
async def test_abandoned_task_fails_when_max_tries_exhausted() -> None:
    """retry_on_crash=True but attempts >= max_tries -> fail."""
    client = _build_mock_client(
        running_members=["exhausted-id"],
        task_hashes={
            "exhausted-id": {
                "operation": "OP_INDEX_DOCUMENT",
                "attempts": "5",  # == max_tries
                "priority": "50",
            }
        },
        heartbeats=set(),
    )
    client.get_retry_policy = MagicMock(return_value=True)

    stats = await reconcile_queue(client, "operations", max_tries=5)

    assert stats.failed_unrecoverable == 1
    assert stats.recovered_crashed == 0
    client.client.zadd.assert_not_awaited()
