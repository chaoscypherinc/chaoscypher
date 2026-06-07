# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for the bulk/maintenance surfaces of ``queue/client.py``.

Complements ``test_client_coverage.py`` with the larger scan-driven methods:

- ``connect_with_retry`` — disabled short-circuit + required-exhaustion raise.
- ``get_recent_tasks_count`` — global + per-queue + no-client.
- ``cancel_tasks_batch`` — empty, not-found, terminal-skip, mixed cancel.
- ``cancel_by_metadata`` — metadata match dispatch (queued delete + running flag).
- ``cancel_all_tasks`` — status filtering + queue filtering.
- ``clear_old_completed_tasks`` — terminal-status sweep + stale-recent cleanup.
- ``_cleanup_stale_recent_list`` — removes ids whose task hash is gone.
- ``clear_all_stats`` — deletes recent lists.
- Stats delegation (``get_queue_stats`` / ``track_tokens`` / ``get_token_stats``
  / ``clear_token_stats`` / ``get_all_stats``) — monitor-present vs. fallback.

Uses the same ``QueueClient.__new__`` + recording fake-Valkey pattern as the
sibling queue tests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.client import QueueClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare_client() -> QueueClient:
    client = QueueClient.__new__(QueueClient)
    client.client = None
    client._connected = True
    client._enabled = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 7200
    client._llm_result_ttl = 3600
    client._failed_result_ttl = 14 * 86_400
    client._cancel_ttl = 300
    client._atomic_complete_sha = None
    client.monitor = None
    client._handlers = {}
    client._retry_policy = {}
    client._transient_retry_policy = {}
    client._queues = set()
    return client


def _make_pipeline() -> tuple[MagicMock, list[dict[str, Any]]]:
    recorded: list[dict[str, Any]] = []
    pipeline = MagicMock()

    def _hset(key: str, mapping: dict[str, Any]) -> MagicMock:
        recorded.append({"op": "hset", "key": key, "mapping": dict(mapping)})
        return pipeline

    def _zrem(key: str, member: str) -> MagicMock:
        recorded.append({"op": "zrem", "key": key, "member": member})
        return pipeline

    def _delete(key: str) -> MagicMock:
        recorded.append({"op": "delete", "key": key})
        return pipeline

    def _set(key: str, *a: Any, **k: Any) -> MagicMock:
        recorded.append({"op": "set", "key": key})
        return pipeline

    def _srem(key: str, member: str) -> MagicMock:
        recorded.append({"op": "srem", "key": key, "member": member})
        return pipeline

    pipeline.hset.side_effect = _hset
    pipeline.zrem.side_effect = _zrem
    pipeline.delete.side_effect = _delete
    pipeline.set.side_effect = _set
    pipeline.srem.side_effect = _srem
    pipeline.exists.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])
    return pipeline, recorded


def _make_client() -> tuple[QueueClient, MagicMock, list[dict[str, Any]]]:
    client = _bare_client()
    pipeline, recorded = _make_pipeline()

    valkey = MagicMock()
    valkey.pipeline = MagicMock(return_value=pipeline)
    valkey.hgetall = AsyncMock(return_value={})
    valkey.delete = AsyncMock(return_value=1)
    valkey.llen = AsyncMock(return_value=0)
    valkey.lrange = AsyncMock(return_value=[])
    valkey.lrem = AsyncMock(return_value=1)

    client.client = valkey
    return client, valkey, recorded


def _hash_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "task_id": "t-1",
        "queue": QUEUE_OPERATIONS,
        "operation": "test_op",
        "status": "queued",
        "priority": "50",
        "created_at": "2026-05-23T00:00:00Z",
        "data": json.dumps({}),
        "metadata": json.dumps({}),
        "result_ttl": "3600",
        "attempts": "0",
        "payload_version": "1",
    }
    payload.update(overrides)
    return payload


def _scan_iter(keys: list[bytes]) -> Any:
    def _factory(*_a: Any, **_k: Any) -> Any:
        async def _gen() -> Any:
            for k in keys:
                yield k

        return _gen()

    return _factory


# ---------------------------------------------------------------------------
# connect_with_retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_with_retry_disabled_returns_false() -> None:
    """When queueing is disabled, connect_with_retry short-circuits to False."""
    client = _bare_client()
    settings = MagicMock()
    settings.queue.connection_max_retries = 3
    settings.queue.connection_retry_delay = 0
    settings.llm.enable_llm_queueing = False

    client.connect = AsyncMock(return_value=False)

    result = await client.connect_with_retry(settings)
    assert result is False


@pytest.mark.asyncio
async def test_connect_with_retry_required_raises_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """required=True raises RuntimeError once all retries fail."""
    import chaoscypher_core.queue.client as client_mod

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)

    client = _bare_client()
    settings = MagicMock()
    settings.queue.connection_max_retries = 2
    settings.queue.connection_retry_delay = 0
    settings.llm.enable_llm_queueing = True

    client.connect = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="connection failed"):
        await client.connect_with_retry(settings, required=True)


@pytest.mark.asyncio
async def test_connect_with_retry_not_required_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """required=False returns False (graceful degradation) after exhaustion."""
    import chaoscypher_core.queue.client as client_mod

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)

    client = _bare_client()
    settings = MagicMock()
    settings.queue.connection_max_retries = 2
    settings.queue.connection_retry_delay = 0
    settings.llm.enable_llm_queueing = True

    client.connect = AsyncMock(return_value=False)

    result = await client.connect_with_retry(settings, required=False)
    assert result is False


@pytest.mark.asyncio
async def test_connect_with_retry_success_first_try() -> None:
    """A successful first connect returns True immediately."""
    client = _bare_client()
    settings = MagicMock()
    settings.queue.connection_max_retries = 3
    settings.queue.connection_retry_delay = 0
    settings.llm.enable_llm_queueing = True

    client.connect = AsyncMock(return_value=True)

    assert await client.connect_with_retry(settings) is True


# ---------------------------------------------------------------------------
# get_recent_tasks_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_count_zero_without_client() -> None:
    client = _bare_client()
    assert await client.get_recent_tasks_count() == 0


@pytest.mark.asyncio
async def test_recent_count_global() -> None:
    client, valkey, _ = _make_client()
    valkey.llen = AsyncMock(return_value=12)
    assert await client.get_recent_tasks_count() == 12


@pytest.mark.asyncio
async def test_recent_count_per_queue_sums() -> None:
    client, valkey, _ = _make_client()
    valkey.llen = AsyncMock(side_effect=[3, 4])
    total = await client.get_recent_tasks_count(queues=["a", "b"])
    assert total == 7


# ---------------------------------------------------------------------------
# cancel_tasks_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_tasks_batch_empty() -> None:
    client, _, _ = _make_client()
    result = await client.cancel_tasks_batch([])
    assert result == {"cancelled": 0, "failed": []}


@pytest.mark.asyncio
async def test_cancel_tasks_batch_not_found_recorded() -> None:
    """A missing task is reported in the failed list."""
    client, valkey, _ = _make_client()
    valkey.hgetall = AsyncMock(return_value={})

    result = await client.cancel_tasks_batch(["gone"])
    assert result["cancelled"] == 0
    assert result["failed"][0]["task_id"] == "gone"


@pytest.mark.asyncio
async def test_cancel_tasks_batch_terminal_skipped() -> None:
    """A task already terminal is silently skipped (not cancelled, not failed)."""
    client, valkey, _ = _make_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="completed"))

    result = await client.cancel_tasks_batch(["t-done"])
    assert result == {"cancelled": 0, "failed": []}


@pytest.mark.asyncio
async def test_cancel_tasks_batch_cancels_active() -> None:
    """A queued task is cancelled via the pipeline and persisted to the DB."""
    client, valkey, recorded = _make_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="queued"))
    client._persist_cancellation_to_db = MagicMock()

    result = await client.cancel_tasks_batch(["t-q"])
    assert result["cancelled"] == 1
    assert any(e["op"] == "hset" and e["mapping"]["status"] == "cancelled" for e in recorded)
    client._persist_cancellation_to_db.assert_called_once()


# ---------------------------------------------------------------------------
# cancel_by_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_by_metadata_no_match_returns_zero() -> None:
    """No metadata match → 0 cancellations."""
    client, valkey, _ = _make_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-1"])
    valkey.hgetall = AsyncMock(
        return_value=_hash_payload(status="queued", metadata=json.dumps({"x": "y"}))
    )

    cancelled = await client.cancel_by_metadata({"x": "nope"})
    assert cancelled == 0


@pytest.mark.asyncio
async def test_cancel_by_metadata_queued_deletes() -> None:
    """A matching queued task is deleted from the keyspace."""
    client, valkey, recorded = _make_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-q"])
    valkey.hgetall = AsyncMock(
        return_value=_hash_payload(
            task_id="t-q", status="queued", metadata=json.dumps({"source_id": "s1"})
        )
    )

    cancelled = await client.cancel_by_metadata({"source_id": "s1"})
    assert cancelled == 1
    assert any(e["op"] == "delete" for e in recorded)


@pytest.mark.asyncio
async def test_cancel_by_metadata_running_sets_flag() -> None:
    """A matching running task gets the cancel flag + SREM + cancelled mark."""
    client, valkey, recorded = _make_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-r"])
    valkey.hgetall = AsyncMock(
        return_value=_hash_payload(
            task_id="t-r", status="running", metadata=json.dumps({"source_id": "s1"})
        )
    )

    cancelled = await client.cancel_by_metadata({"source_id": "s1"})
    assert cancelled == 1
    assert any(e["op"] == "set" for e in recorded)
    assert any(e["op"] == "srem" for e in recorded)


# ---------------------------------------------------------------------------
# cancel_all_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_all_tasks_none_to_cancel() -> None:
    """All tasks terminal → returns 0."""
    client, valkey, _ = _make_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-1"])
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="completed"))

    assert await client.cancel_all_tasks() == 0


@pytest.mark.asyncio
async def test_cancel_all_tasks_queue_filtered() -> None:
    """Only tasks in the requested queue are cancelled."""
    client, valkey, recorded = _make_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-a", b"queue:task:t-b"])
    valkey.hgetall = AsyncMock(
        side_effect=[
            _hash_payload(task_id="t-a", queue=QUEUE_OPERATIONS, status="queued"),
            _hash_payload(task_id="t-b", queue="llm", status="queued"),
        ]
    )

    cancelled = await client.cancel_all_tasks(queue=QUEUE_OPERATIONS)
    assert cancelled == 1


# ---------------------------------------------------------------------------
# clear_old_completed_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_old_completed_tasks_removes_terminal() -> None:
    """A completed task with no time threshold is removed."""
    client, valkey, _ = _make_client()
    # First scan_iter is the task sweep; the recent-list cleanup uses lrange.
    valkey.scan_iter = _scan_iter([b"queue:task:t-done"])
    valkey.hgetall = AsyncMock(return_value=_hash_payload(task_id="t-done", status="completed"))
    valkey.lrange = AsyncMock(return_value=[])

    removed = await client.clear_old_completed_tasks(queue=QUEUE_OPERATIONS)
    assert removed == 1
    valkey.delete.assert_awaited()


@pytest.mark.asyncio
async def test_clear_old_completed_tasks_skips_active() -> None:
    """A still-queued task is not removed by the sweep."""
    client, valkey, _ = _make_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-q"])
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="queued"))
    valkey.lrange = AsyncMock(return_value=[])

    removed = await client.clear_old_completed_tasks(queue=QUEUE_OPERATIONS)
    assert removed == 0


# ---------------------------------------------------------------------------
# _cleanup_stale_recent_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_stale_recent_list_removes_missing() -> None:
    """Ids whose task hash no longer exists are LREM'd from the recent list."""
    client, valkey, _ = _make_client()
    valkey.lrange = AsyncMock(return_value=[b"t-a", b"t-b"])

    pipeline = MagicMock()
    pipeline.exists.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[0, 1])  # t-a gone, t-b present
    valkey.pipeline = MagicMock(return_value=pipeline)

    await client._cleanup_stale_recent_list("queue:recent")
    valkey.lrem.assert_awaited()  # one stale id removed


@pytest.mark.asyncio
async def test_cleanup_stale_recent_list_empty_noop() -> None:
    """An empty recent list is a no-op."""
    client, valkey, _ = _make_client()
    valkey.lrange = AsyncMock(return_value=[])
    await client._cleanup_stale_recent_list("queue:recent")
    valkey.lrem.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_stale_recent_list_no_client() -> None:
    """No client → silent return."""
    client = _bare_client()
    await client._cleanup_stale_recent_list("queue:recent")  # no error


# ---------------------------------------------------------------------------
# clear_all_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_all_stats_deletes_recent_lists() -> None:
    """clear_all_stats deletes the global and per-queue recent lists."""
    client, valkey, _ = _make_client()
    valkey.scan_iter = _scan_iter([b"queue:operations:recent"])

    await client.clear_all_stats()
    # Global "queue:recent" delete + the scanned per-queue list delete.
    assert valkey.delete.await_count >= 2


# ---------------------------------------------------------------------------
# Stats delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_queue_stats_fallback_without_monitor() -> None:
    """Without a monitor, get_queue_stats returns a zeroed default dict."""
    client, _, _ = _make_client()
    client.monitor = None
    stats = await client.get_queue_stats(QUEUE_OPERATIONS)
    assert stats["queue"] == QUEUE_OPERATIONS
    assert stats["queued"] == 0


@pytest.mark.asyncio
async def test_get_queue_stats_delegates_to_monitor() -> None:
    """With a monitor, get_queue_stats delegates."""
    client, _, _ = _make_client()
    monitor = MagicMock()
    monitor.get_queue_stats = AsyncMock(return_value={"queue": "x", "queued": 5})
    client.monitor = monitor

    stats = await client.get_queue_stats("x")
    assert stats["queued"] == 5


@pytest.mark.asyncio
async def test_track_tokens_delegates_when_monitor_present() -> None:
    client, _, _ = _make_client()
    monitor = MagicMock()
    monitor.track_tokens = AsyncMock(return_value=None)
    client.monitor = monitor

    await client.track_tokens(QUEUE_OPERATIONS, 10, 20, 0.5)
    monitor.track_tokens.assert_awaited_once()


@pytest.mark.asyncio
async def test_track_tokens_noop_without_monitor() -> None:
    client, _, _ = _make_client()
    client.monitor = None
    await client.track_tokens(QUEUE_OPERATIONS, 1, 2)  # no error


@pytest.mark.asyncio
async def test_get_token_stats_fallback_and_delegate() -> None:
    client, _, _ = _make_client()
    client.monitor = None
    fallback = await client.get_token_stats(QUEUE_OPERATIONS)
    assert fallback["total_tokens"] == 0

    monitor = MagicMock()
    monitor.get_token_stats = AsyncMock(return_value={"total_tokens": 99})
    client.monitor = monitor
    delegated = await client.get_token_stats(QUEUE_OPERATIONS)
    assert delegated["total_tokens"] == 99


@pytest.mark.asyncio
async def test_clear_token_stats_delegates() -> None:
    client, _, _ = _make_client()
    monitor = MagicMock()
    monitor.clear_token_stats = AsyncMock(return_value=None)
    client.monitor = monitor
    await client.clear_token_stats()
    monitor.clear_token_stats.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_all_stats_fallback_and_delegate() -> None:
    client, _, _ = _make_client()
    client.monitor = None
    assert await client.get_all_stats() == []

    monitor = MagicMock()
    monitor.get_all_stats = AsyncMock(return_value=[{"queue": "x"}])
    client.monitor = monitor
    assert await client.get_all_stats() == [{"queue": "x"}]


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_closes_client() -> None:
    """Disconnect aclose()s the connection and clears state."""
    client, valkey, _ = _make_client()
    valkey.aclose = AsyncMock(return_value=None)

    await client.disconnect()
    valkey.aclose.assert_awaited_once()
    assert client.client is None
    assert client._connected is False


@pytest.mark.asyncio
async def test_disconnect_noop_when_already_disconnected() -> None:
    """Disconnect on an already-null client is a clean no-op."""
    client = _bare_client()
    client.client = None
    await client.disconnect()
    assert client._connected is False
