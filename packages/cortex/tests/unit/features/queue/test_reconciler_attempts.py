# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Task 5.6: reconciler must increment attempts only after a successful requeue.

Three invariants under test:

1. zadd failure → attempts unchanged, task not failed (next reconcile retries).
2. Happy path → zadd succeeds, attempts increments by 1.
3. Max-tries exhausted → task marked failed, zadd never called.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.reconciler import ReconcileStats, _handle_abandoned


def _build_mock_client(
    *,
    task_hash: dict[str, str],
    zadd_raises: Exception | None = None,
) -> MagicMock:
    """Build a minimal mock QueueClient for _handle_abandoned tests.

    Args:
        task_hash: Fields stored in queue:task:{task_id}.
        zadd_raises: If set, valkey.zadd raises this exception.
    """
    client = MagicMock()
    valkey = MagicMock()
    client.client = valkey

    def _hgetall_side_effect(key: str) -> dict[bytes, bytes]:
        if isinstance(key, bytes):
            key = key.decode()
        return {k.encode(): v.encode() for k, v in task_hash.items()}

    valkey.hgetall = AsyncMock(side_effect=_hgetall_side_effect)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=1)
    # PERSIST/EXPIRE for the dead-letter retention path.
    valkey.persist = AsyncMock(return_value=True)
    valkey.expire = AsyncMock(return_value=True)

    if zadd_raises is not None:
        valkey.zadd = AsyncMock(side_effect=zadd_raises)
    else:
        valkey.zadd = AsyncMock(return_value=1)

    # Default retry policy: allow retries
    client.get_retry_policy = MagicMock(return_value=True)

    # ``QueueClient.mark_task_failed_terminal`` proxy: route HSET + EXPIRE
    # through the mock so existing HSET assertions still see the failed
    # status write, and the EXPIRE recorder works for retention asserts.
    client._failed_result_ttl = 14 * 86_400

    async def _mark_failed(task_id: str, fields: dict[str, str]) -> None:
        await valkey.hset(f"queue:task:{task_id}", mapping=fields)
        await valkey.expire(f"queue:task:{task_id}", client._failed_result_ttl)

    client.mark_task_failed_terminal = AsyncMock(side_effect=_mark_failed)
    client.failed_result_ttl = client._failed_result_ttl

    # Emulate the guarded-write primitives (requeue_atomic.lua /
    # guarded_status_write.lua) against the fixture hash, routing the
    # component operations through the same valkey recorder the assertions
    # inspect — a ``zadd_raises`` fixture therefore still aborts the move
    # exactly like a transient Valkey error inside the real call would.
    async def _requeue_atomic(queue_name: str, task_id: str, priority: float) -> str:
        if not task_hash:
            return "__missing__"
        status = task_hash.get("status", "running")
        if status in {"completed", "cancelled"}:
            return status
        await valkey.hset(
            f"queue:task:{task_id}",
            mapping={"status": "queued", "error": "", "error_type": ""},
        )
        await valkey.persist(f"queue:task:{task_id}")
        await valkey.zadd(f"queue:{queue_name}:pending", {task_id: priority})
        await valkey.srem(f"queue:{queue_name}:running", task_id)
        await valkey.hincrby(f"queue:task:{task_id}", "attempts", 1)
        return "__ok__"

    client.requeue_task_atomic = AsyncMock(side_effect=_requeue_atomic)

    async def _guarded_status_write(
        task_id: str,
        *,
        new_status: str,
        allowed_from: tuple[str, ...],
        extra_fields: dict | None = None,
        remove_from: str | None = None,
        removal: str = "none",
        expire_seconds: int | None = None,
    ) -> str:
        if not task_hash:
            return "__missing__"
        status = task_hash.get("status", "running")
        if status not in allowed_from:
            return status
        await valkey.hset(
            f"queue:task:{task_id}", mapping={"status": new_status, **(extra_fields or {})}
        )
        if remove_from and removal == "srem":
            await valkey.srem(remove_from, task_id)
        if expire_seconds is not None:
            await valkey.expire(f"queue:task:{task_id}", expire_seconds)
        return "__ok__"

    client.guarded_status_write = AsyncMock(side_effect=_guarded_status_write)

    return client


@pytest.mark.asyncio
async def test_handle_abandoned_does_not_increment_attempts_on_zadd_failure() -> None:
    """If zadd raises, attempts counter must stay at its prior value.

    A transient Valkey outage during the zadd must not silently consume the
    task's retry budget — the task should remain at attempts=2 so the next
    reconcile cycle re-tries it fresh.
    """
    client = _build_mock_client(
        task_hash={
            "operation": "OP_INDEX_DOCUMENT",
            "attempts": "2",
            "priority": "50",
            "max_tries": "5",
        },
        zadd_raises=ConnectionError("Valkey unavailable"),
    )
    stats = ReconcileStats()

    await _handle_abandoned(
        client=client,
        queue_name="operations",
        task_id="test-task-id",
        max_tries=5,
        stats=stats,
    )

    # hincrby must NOT have been called — attempts budget is intact
    client.client.hincrby.assert_not_awaited()

    # Task must not have been marked failed either — it should stay retryable
    hset_calls = client.client.hset.await_args_list
    assert not any("failed" in str(c) for c in hset_calls), (
        "Task must not be marked failed when zadd raises transiently"
    )

    # Stats must not count this as recovered or failed
    assert stats.recovered_crashed == 0
    assert stats.failed_unrecoverable == 0


@pytest.mark.asyncio
async def test_handle_abandoned_increments_attempts_on_successful_requeue() -> None:
    """Happy path: zadd succeeds, attempts increments by 1.

    After a successful requeue the attempts counter must reflect one more
    processing attempt so the retry budget is correctly tracked.
    """
    client = _build_mock_client(
        task_hash={
            "operation": "OP_INDEX_DOCUMENT",
            "attempts": "2",
            "priority": "50",
            "max_tries": "5",
        },
    )
    stats = ReconcileStats()

    await _handle_abandoned(
        client=client,
        queue_name="operations",
        task_id="test-task-id",
        max_tries=5,
        stats=stats,
    )

    # zadd must have been called (task requeued)
    client.client.zadd.assert_awaited_once()

    # hincrby must follow the zadd, incrementing attempts by exactly 1
    client.client.hincrby.assert_awaited_once_with("queue:task:test-task-id", "attempts", 1)

    # Stats should count one recovered task
    assert stats.recovered_crashed == 1
    assert stats.failed_unrecoverable == 0


@pytest.mark.asyncio
async def test_handle_abandoned_marks_failed_at_max_tries() -> None:
    """When attempts has reached max_tries, mark failed instead of re-enqueueing.

    The task has exhausted its retry budget so it must be permanently failed
    without calling zadd or hincrby.
    """
    client = _build_mock_client(
        task_hash={
            "operation": "OP_INDEX_DOCUMENT",
            "attempts": "5",
            "priority": "50",
            "max_tries": "5",
        },
    )
    stats = ReconcileStats()

    await _handle_abandoned(
        client=client,
        queue_name="operations",
        task_id="exhausted-task-id",
        max_tries=5,
        stats=stats,
    )

    # Task exhausted retries → zadd must NOT be called
    client.client.zadd.assert_not_awaited()

    # hincrby must NOT be called either
    client.client.hincrby.assert_not_awaited()

    # Task must be marked failed
    hset_calls = client.client.hset.await_args_list
    assert any("failed" in str(c) for c in hset_calls), (
        f"Expected hset marking task as failed, got {hset_calls}"
    )

    assert stats.failed_unrecoverable == 1
    assert stats.recovered_crashed == 0


@pytest.mark.asyncio
async def test_handle_abandoned_requeue_is_one_atomic_move() -> None:
    """The requeue delegates to ONE atomic Lua move — no bare sequence.

    The zadd-before-srem / hincrby-after-zadd ordering invariants the old
    Python sequence enforced now live inside ``requeue_atomic.lua``, so
    ``_handle_abandoned`` must issue exactly one ``requeue_task_atomic``
    call and never touch zadd/srem/hincrby directly on the requeue path.
    """
    client = MagicMock()
    valkey = MagicMock()
    client.client = valkey

    task_hash = {
        "operation": "OP_INDEX_DOCUMENT",
        "attempts": "1",
        "priority": "50",
    }

    async def _hgetall(_key: str) -> dict[bytes, bytes]:
        return {k.encode(): v.encode() for k, v in task_hash.items()}

    valkey.hgetall = AsyncMock(side_effect=_hgetall)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)
    valkey.zadd = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=2)

    client.get_retry_policy = MagicMock(return_value=True)
    client.requeue_task_atomic = AsyncMock(return_value="__ok__")
    stats = ReconcileStats()

    await _handle_abandoned(
        client=client,
        queue_name="operations",
        task_id="order-test-id",
        max_tries=5,
        stats=stats,
    )

    client.requeue_task_atomic.assert_awaited_once_with("operations", "order-test-id", 50.0)
    valkey.zadd.assert_not_awaited()
    valkey.srem.assert_not_awaited()
    valkey.hincrby.assert_not_awaited()
    assert stats.recovered_crashed == 1


@pytest.mark.asyncio
async def test_handle_abandoned_leaves_task_in_running_on_zadd_failure() -> None:
    """If zadd raises, the task must stay in the running set.

    The reconciler only scans ``queue:{queue}:running``. If srem ran before a
    failed zadd, the task would be in neither the running set nor the pending
    queue — an undetectable limbo no future reconcile cycle could recover.
    srem must therefore run only after zadd succeeds, so a transient Valkey
    error during zadd leaves the task in the running set for the next cycle.
    """
    client = _build_mock_client(
        task_hash={
            "operation": "OP_INDEX_DOCUMENT",
            "attempts": "2",
            "priority": "50",
            "max_tries": "5",
        },
        zadd_raises=ConnectionError("Valkey unavailable"),
    )
    stats = ReconcileStats()

    await _handle_abandoned(
        client=client,
        queue_name="operations",
        task_id="test-task-id",
        max_tries=5,
        stats=stats,
    )

    # srem must NOT have run — the task stays in running for the next cycle.
    client.client.srem.assert_not_awaited()

    # And it is neither recovered nor failed this pass.
    assert stats.recovered_crashed == 0
    assert stats.failed_unrecoverable == 0


@pytest.mark.asyncio
async def test_handle_abandoned_terminal_fail_is_one_guarded_write() -> None:
    """Terminal failure delegates to ONE guarded write — no srem-then-hset.

    The old sequence SREM'd the task out of the running set before the
    terminal HSET; a transient failure between the two leaked a
    half-written hash no future cycle would revisit (2026-06-14 finding).
    The guarded write does mark-failed + SREM + dead-letter TTL atomically.
    """
    client = MagicMock()
    valkey = MagicMock()
    client.client = valkey

    task_hash = {
        "operation": "OP_INDEX_DOCUMENT",
        "attempts": "1",
        "priority": "50",
    }

    async def _hgetall(_key: str) -> dict[bytes, bytes]:
        return {k.encode(): v.encode() for k, v in task_hash.items()}

    valkey.hgetall = AsyncMock(side_effect=_hgetall)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)

    client.get_retry_policy = MagicMock(return_value=False)
    client.failed_result_ttl = 14 * 86_400
    client.guarded_status_write = AsyncMock(return_value="__ok__")
    stats = ReconcileStats()

    await _handle_abandoned(
        client=client,
        queue_name="operations",
        task_id="order-test-id",
        max_tries=5,
        stats=stats,
    )

    client.guarded_status_write.assert_awaited_once()
    kwargs = client.guarded_status_write.await_args.kwargs
    assert kwargs["new_status"] == "failed"
    assert kwargs["removal"] == "srem"
    assert kwargs["remove_from"] == "queue:operations:running"
    assert kwargs["expire_seconds"] == client.failed_result_ttl
    # No bare srem/hset outside the guarded write on the terminal path.
    valkey.srem.assert_not_awaited()
    valkey.hset.assert_not_awaited()
    assert stats.failed_unrecoverable == 1
