# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for ``queue/reconciler.py``.

Drives ``reconcile_queue`` and its ``_handle_abandoned`` helper across the full
classification matrix using an AsyncMock-backed fake ``QueueClient``:

- no-client guard (``client.client is None``) -> skip, empty stats.
- orphan: running-set ID with neither hash nor heartbeat -> SREM + counted.
- healthy: hash + heartbeat present, not timed out -> skipped.
- abandoned (heartbeat gone): requeue-allowed vs. requeue-denied vs.
  attempts-exhausted vs. zadd-failure (attempts NOT consumed).
- absolute-timeout abandon: hash present, ``started_at`` older than cutoff,
  even when the heartbeat is still alive.
- ``started_at`` malformed / empty -> not timed out.
- ``_persist_counters`` accumulates only non-zero fields into the recovery hash.
- ``ReconcileStats`` arithmetic helpers (total/merge/to_dict).

The fake Valkey backend mirrors the recording-AsyncMock pattern used by the
sibling queue tests; ``QueueClient`` is constructed via ``__new__`` so no real
Valkey connection is required.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.reconciler import (
    ReconcileStats,
    _handle_abandoned,
    _persist_counters,
    _recovery_counters_key,
    reconcile_queue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valkey() -> MagicMock:
    """Build a recording fake async Valkey client for the reconciler."""
    valkey = MagicMock()
    valkey.smembers = AsyncMock(return_value=set())
    valkey.exists = AsyncMock(return_value=1)
    valkey.hgetall = AsyncMock(return_value={})
    valkey.hset = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.persist = AsyncMock(return_value=True)
    valkey.zadd = AsyncMock(return_value=1)
    return valkey


def _make_client(*, retry_policy: bool = True) -> MagicMock:
    """Build a fake QueueClient with a live Valkey backend and retry policy."""
    client = MagicMock()
    client.client = _make_valkey()
    client.get_retry_policy = MagicMock(return_value=retry_policy)
    client.mark_task_failed_terminal = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# ReconcileStats
# ---------------------------------------------------------------------------


def test_reconcile_stats_total_and_to_dict() -> None:
    """total() sums all counters; to_dict() serialises every field."""
    stats = ReconcileStats(recovered_orphans=2, recovered_crashed=3, failed_unrecoverable=4)
    assert stats.total() == 9
    assert stats.to_dict() == {
        "recovered_orphans": 2,
        "recovered_crashed": 3,
        "failed_unrecoverable": 4,
    }


def test_reconcile_stats_merge_accumulates() -> None:
    """merge() folds another stats object's counters into this one."""
    a = ReconcileStats(recovered_orphans=1, recovered_crashed=1, failed_unrecoverable=1)
    b = ReconcileStats(recovered_orphans=10, recovered_crashed=20, failed_unrecoverable=30)
    a.merge(b)
    assert a.to_dict() == {
        "recovered_orphans": 11,
        "recovered_crashed": 21,
        "failed_unrecoverable": 31,
    }


def test_recovery_counters_key_namespaced() -> None:
    """The recovery counters key is namespaced per queue."""
    assert _recovery_counters_key(QUEUE_OPERATIONS) == f"queue:{QUEUE_OPERATIONS}:recovery_counters"


# ---------------------------------------------------------------------------
# reconcile_queue — guards / classifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_skipped_when_no_client() -> None:
    """A client with no live Valkey connection returns empty stats and skips."""
    client = MagicMock()
    client.client = None
    stats = await reconcile_queue(client, QUEUE_OPERATIONS)
    assert stats.total() == 0


@pytest.mark.asyncio
async def test_reconcile_empty_running_set_returns_clean() -> None:
    """An empty running set produces all-zero stats and never persists counters."""
    client = _make_client()
    client.client.smembers = AsyncMock(return_value=set())
    stats = await reconcile_queue(client, QUEUE_OPERATIONS)
    assert stats.total() == 0
    client.client.hincrby.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_orphan_removed() -> None:
    """An ID with neither hash nor heartbeat is removed and counted as orphan."""
    client = _make_client()
    client.client.smembers = AsyncMock(return_value={b"orphan-1"})
    # Both exists() calls return 0 (hash missing AND heartbeat missing).
    client.client.exists = AsyncMock(return_value=0)

    stats = await reconcile_queue(client, QUEUE_OPERATIONS)

    assert stats.recovered_orphans == 1
    client.client.srem.assert_any_await(f"queue:{QUEUE_OPERATIONS}:running", "orphan-1")
    # Non-zero stat -> counter persisted.
    client.client.hincrby.assert_any_await(
        _recovery_counters_key(QUEUE_OPERATIONS), "recovered_orphans", 1
    )


@pytest.mark.asyncio
async def test_reconcile_healthy_task_skipped() -> None:
    """A task with both hash and heartbeat present (no timeout) is left alone."""
    client = _make_client()
    client.client.smembers = AsyncMock(return_value={b"healthy-1"})
    client.client.exists = AsyncMock(return_value=1)  # both hash + heartbeat present

    stats = await reconcile_queue(client, QUEUE_OPERATIONS)

    assert stats.total() == 0
    client.client.srem.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_abandoned_heartbeat_gone_requeues() -> None:
    """Hash present but heartbeat gone, retry allowed, attempts left -> requeue."""
    client = _make_client(retry_policy=True)
    client.client.smembers = AsyncMock(return_value={b"task-1"})

    # First exists() = hash present (1), second = heartbeat absent (0).
    client.client.exists = AsyncMock(side_effect=[1, 0])
    client.client.hgetall = AsyncMock(
        return_value={b"operation": b"op", b"attempts": b"0", b"priority": b"50"}
    )

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3)

    assert stats.recovered_crashed == 1
    client.client.srem.assert_any_await(f"queue:{QUEUE_OPERATIONS}:running", "task-1")
    client.client.zadd.assert_awaited()  # re-added to pending
    client.client.hincrby.assert_any_await("queue:task:task-1", "attempts", 1)


@pytest.mark.asyncio
async def test_reconcile_abandoned_requeue_denied_fails() -> None:
    """Hash present, heartbeat gone, retry DENIED -> mark failed terminally."""
    client = _make_client(retry_policy=False)
    client.client.smembers = AsyncMock(return_value={b"task-2"})
    client.client.exists = AsyncMock(side_effect=[1, 0])
    client.client.hgetall = AsyncMock(
        return_value={b"operation": b"op", b"attempts": b"0", b"priority": b"50"}
    )

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3)

    assert stats.failed_unrecoverable == 1
    client.mark_task_failed_terminal.assert_awaited_once()
    # Error message reflects the denied policy.
    _tid, fields = client.mark_task_failed_terminal.call_args.args
    assert "denies retry" in fields["error"]
    assert fields["error_type"] == "worker_crashed"


@pytest.mark.asyncio
async def test_reconcile_abandoned_attempts_exhausted_fails() -> None:
    """Retry allowed but attempts == max_tries -> mark failed terminally."""
    client = _make_client(retry_policy=True)
    client.client.smembers = AsyncMock(return_value={b"task-3"})
    client.client.exists = AsyncMock(side_effect=[1, 0])
    client.client.hgetall = AsyncMock(
        return_value={b"operation": b"op", b"attempts": b"5", b"priority": b"50"}
    )

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=5)

    assert stats.failed_unrecoverable == 1
    _tid, fields = client.mark_task_failed_terminal.call_args.args
    assert "after 5 attempts" in fields["error"]


@pytest.mark.asyncio
async def test_reconcile_requeue_zadd_failure_preserves_attempts() -> None:
    """A zadd failure during requeue logs and leaves attempts unchanged."""
    client = _make_client(retry_policy=True)
    client.client.smembers = AsyncMock(return_value={b"task-z"})
    client.client.exists = AsyncMock(side_effect=[1, 0])
    client.client.hgetall = AsyncMock(
        return_value={b"operation": b"op", b"attempts": b"1", b"priority": b"50"}
    )
    client.client.zadd = AsyncMock(side_effect=RuntimeError("valkey down"))

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3)

    # Requeue aborted before incrementing attempts.
    assert stats.recovered_crashed == 0
    client.client.hincrby.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_absolute_timeout_abandons_despite_heartbeat() -> None:
    """An old started_at past the cutoff abandons the task even if heartbeat alive."""
    client = _make_client(retry_policy=True)
    client.client.smembers = AsyncMock(return_value={b"slow-1"})
    # hash present (1) AND heartbeat present (1) -> only the timeout check abandons it.
    client.client.exists = AsyncMock(return_value=1)

    old_started = (datetime.now(UTC) - timedelta(seconds=10_000)).isoformat()
    client.client.hgetall = AsyncMock(
        return_value={
            b"operation": b"op",
            b"attempts": b"0",
            b"priority": b"50",
            b"started_at": old_started.encode(),
        }
    )

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3, timeout_seconds=60)

    # Timed out -> abandoned -> requeued (retry allowed, attempts left).
    assert stats.recovered_crashed == 1


@pytest.mark.asyncio
async def test_reconcile_recent_started_at_not_timed_out() -> None:
    """A fresh started_at within the cutoff is NOT abandoned (healthy)."""
    client = _make_client()
    client.client.smembers = AsyncMock(return_value={b"fresh-1"})
    client.client.exists = AsyncMock(return_value=1)  # hash + heartbeat present

    recent = datetime.now(UTC).isoformat()
    client.client.hgetall = AsyncMock(
        return_value={
            b"operation": b"op",
            b"attempts": b"0",
            b"priority": b"50",
            b"started_at": recent.encode(),
        }
    )

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3, timeout_seconds=3600)

    assert stats.total() == 0


@pytest.mark.asyncio
async def test_reconcile_malformed_started_at_not_timed_out() -> None:
    """A non-ISO started_at value is treated as unknown and not timed out."""
    client = _make_client()
    client.client.smembers = AsyncMock(return_value={b"bad-ts"})
    client.client.exists = AsyncMock(return_value=1)
    client.client.hgetall = AsyncMock(
        return_value={
            b"operation": b"op",
            b"attempts": b"0",
            b"priority": b"50",
            b"started_at": b"not-a-timestamp",
        }
    )

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3, timeout_seconds=60)

    # Parsing failed -> falls through to healthy skip.
    assert stats.total() == 0


@pytest.mark.asyncio
async def test_reconcile_empty_started_at_skips_timeout_check() -> None:
    """An empty started_at string short-circuits the timeout branch."""
    client = _make_client()
    client.client.smembers = AsyncMock(return_value={b"no-start"})
    client.client.exists = AsyncMock(return_value=1)
    client.client.hgetall = AsyncMock(
        return_value={b"operation": b"op", b"attempts": b"0", b"priority": b"50"}
    )

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3, timeout_seconds=60)

    assert stats.total() == 0


@pytest.mark.asyncio
async def test_reconcile_decodes_str_task_ids() -> None:
    """Task IDs that are already str (not bytes) are handled without decode error."""
    client = _make_client()
    client.client.smembers = AsyncMock(return_value={"str-orphan"})
    client.client.exists = AsyncMock(return_value=0)

    stats = await reconcile_queue(client, QUEUE_OPERATIONS)

    assert stats.recovered_orphans == 1
    client.client.srem.assert_any_await(f"queue:{QUEUE_OPERATIONS}:running", "str-orphan")


# ---------------------------------------------------------------------------
# _handle_abandoned — direct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_abandoned_noop_when_no_client() -> None:
    """_handle_abandoned returns immediately if the Valkey connection is gone."""
    client = _make_client()
    client.client = None
    stats = ReconcileStats()
    # Should not raise.
    await _handle_abandoned(
        client=client,
        queue_name=QUEUE_OPERATIONS,
        task_id="t-x",
        max_tries=3,
        stats=stats,
    )
    assert stats.total() == 0


# ---------------------------------------------------------------------------
# _persist_counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_counters_noop_when_total_zero() -> None:
    """No counters are written when nothing was recovered."""
    client = _make_client()
    await _persist_counters(client, QUEUE_OPERATIONS, ReconcileStats())
    client.client.hincrby.assert_not_called()


@pytest.mark.asyncio
async def test_persist_counters_noop_when_no_client() -> None:
    """No counters are written when the Valkey connection is missing."""
    client = _make_client()
    client.client = None
    # total > 0 but client gone -> early return, no error.
    await _persist_counters(client, QUEUE_OPERATIONS, ReconcileStats(recovered_orphans=1))


@pytest.mark.asyncio
async def test_persist_counters_writes_only_nonzero_fields() -> None:
    """Only fields with a positive value are HINCRBY'd into the recovery hash."""
    client = _make_client()
    stats = ReconcileStats(recovered_orphans=2, recovered_crashed=0, failed_unrecoverable=5)

    await _persist_counters(client, QUEUE_OPERATIONS, stats)

    key = _recovery_counters_key(QUEUE_OPERATIONS)
    client.client.hincrby.assert_any_await(key, "recovered_orphans", 2)
    client.client.hincrby.assert_any_await(key, "failed_unrecoverable", 5)
    # recovered_crashed == 0 must NOT be written.
    for call in client.client.hincrby.await_args_list:
        assert call.args[1] != "recovered_crashed"
