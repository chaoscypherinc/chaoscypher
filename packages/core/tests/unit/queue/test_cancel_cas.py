# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cancellation paths route through the guarded-write CAS primitive.

Pins the 2026-07-12/07-05 hunter findings' fixes (verdicts 2026-07-23):

- ``cancel_task`` queued-path is a guarded zrem+write; a task that raced
  queued→running falls through to the running path instead of clobbering.
- The P1: a cancel racing completion LOSES — a task whose guarded write
  observes ``completed`` keeps that status and its result reachable.
- ``cancel_tasks_batch`` sets the cooperative cancel flag for running
  tasks (previously it set none) and uses guarded writes.
- ``cancel_by_metadata`` never deletes a task that raced to running —
  the guarded delete refuses and the task gets a running-path cancel.

Harness mirrors ``test_client_coverage.py``'s ``QueueClient.__new__`` +
AsyncMock backend pattern (no sibling-test imports under importlib mode).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.client import QueueClient


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
    client._guarded_status_write_sha = None
    client._guarded_delete_sha = None
    client._requeue_atomic_sha = None
    client._string_cas_sha = None
    client.monitor = None
    client._handlers = {}
    client._retry_policy = {}
    client._transient_retry_policy = {}
    client._queues = set()
    return client


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


def _make_client(
    *,
    status: str,
    evalsha: object | list[object] = b"__ok__",
) -> tuple[QueueClient, MagicMock]:
    client = _bare_client()
    valkey = MagicMock()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status=status))
    valkey.set = AsyncMock(return_value=True)
    valkey.hset = AsyncMock(return_value=1)
    valkey.script_load = AsyncMock(return_value="sha-abc")
    if isinstance(evalsha, list):
        valkey.evalsha = AsyncMock(side_effect=evalsha)
    else:
        valkey.evalsha = AsyncMock(return_value=evalsha)
    client.client = valkey
    client._persist_cancellation_to_db = MagicMock()  # type: ignore[method-assign]
    return client, valkey


# ---------------------------------------------------------------------------
# cancel_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_queued_uses_guarded_zrem_write() -> None:
    """Queued cancel is one guarded zrem+write, not a read-then-pipeline."""
    client, valkey = _make_client(status="queued")

    assert await client.cancel_task("t-1") is True

    valkey.evalsha.assert_awaited_once()
    args = valkey.evalsha.await_args.args
    assert args[2] == "queue:task:t-1"
    assert args[3] == f"queue:{QUEUE_OPERATIONS}:pending"
    assert args[5] == "queued"  # allowed_from
    assert args[6] == "zrem"
    valkey.hset.assert_not_awaited()  # no unguarded status write


@pytest.mark.asyncio
async def test_cancel_queued_raced_to_running_falls_through() -> None:
    """A queued cancel that lost to promotion runs the running-path cancel."""
    client, valkey = _make_client(status="queued", evalsha=[b"running", b"__ok__"])

    assert await client.cancel_task("t-1") is True

    # Cooperative flag + durable persist for the now-running task.
    valkey.set.assert_awaited_once_with("queue:cancel:t-1", "1", ex=300)
    client._persist_cancellation_to_db.assert_called_once()  # type: ignore[attr-defined]
    # Second guarded write targets the running set.
    assert valkey.evalsha.await_count == 2
    args = valkey.evalsha.await_args_list[1].args
    assert args[3] == f"queue:{QUEUE_OPERATIONS}:running"
    assert args[5] == "queued,running"
    assert args[6] == "srem"


@pytest.mark.asyncio
async def test_cancel_running_completed_in_window_loses() -> None:
    """P1 pin: a task that completed mid-cancel keeps status=completed.

    The guarded write refuses (returns the current status) and NO
    unguarded ``status=cancelled`` HSET follows — the result stays
    reachable to pollers. Cancel-vs-complete precedence: cancel loses.
    """
    client, valkey = _make_client(status="running", evalsha=b"completed")

    assert await client.cancel_task("t-1") is True

    valkey.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_running_missing_hash_returns_false() -> None:
    client, _valkey = _make_client(status="running", evalsha=b"__missing__")
    assert await client.cancel_task("t-1") is False


# ---------------------------------------------------------------------------
# cancel_tasks_batch
# ---------------------------------------------------------------------------


def _get_task_side_effect(tasks: dict[str, dict[str, Any]]):
    async def _hgetall(key: str) -> dict[str, str]:
        task_id = key.rsplit(":", maxsplit=1)[-1]
        return tasks.get(task_id, {})

    return _hgetall


@pytest.mark.asyncio
async def test_cancel_batch_sets_flag_for_running_and_guards_writes() -> None:
    """Running tasks get the cooperative cancel flag (previously none)."""
    client, valkey = _make_client(status="queued")
    tasks = {
        "t-q": _hash_payload(task_id="t-q", status="queued"),
        "t-r": _hash_payload(task_id="t-r", status="running"),
    }
    valkey.hgetall = AsyncMock(side_effect=_get_task_side_effect(tasks))

    result = await client.cancel_tasks_batch(["t-q", "t-r"])

    assert result["cancelled"] == 2
    valkey.set.assert_awaited_once_with("queue:cancel:t-r", "1", ex=300)
    # Two guarded writes, no unguarded pipeline hset.
    assert valkey.evalsha.await_count == 2
    modes = [call.args[6] for call in valkey.evalsha.await_args_list]
    assert sorted(modes) == ["srem", "zrem"]


@pytest.mark.asyncio
async def test_cancel_batch_completed_in_window_not_counted() -> None:
    """A task whose guarded write observes completed is not counted cancelled."""
    client, valkey = _make_client(status="queued", evalsha=b"completed")
    tasks = {"t-r": _hash_payload(task_id="t-r", status="running")}
    valkey.hgetall = AsyncMock(side_effect=_get_task_side_effect(tasks))

    result = await client.cancel_tasks_batch(["t-r"])

    assert result["cancelled"] == 0
    assert result["failed"] and result["failed"][0]["task_id"] == "t-r"


# ---------------------------------------------------------------------------
# cancel_by_metadata
# ---------------------------------------------------------------------------


def _scan_iter_for(keys: list[str]):
    async def _scan(match: str = "*"):
        for key in keys:
            yield key

    return MagicMock(side_effect=_scan)


@pytest.mark.asyncio
async def test_cancel_by_metadata_guarded_delete_for_queued() -> None:
    client, valkey = _make_client(status="queued")
    tasks = {"t-q": _hash_payload(task_id="t-q", status="queued")}
    valkey.hgetall = AsyncMock(side_effect=_get_task_side_effect(tasks))
    valkey.scan_iter = _scan_iter_for(["queue:task:t-q"])

    cancelled = await client.cancel_by_metadata({"database_name": None})

    assert cancelled == 1
    args = valkey.evalsha.await_args.args
    assert args[2] == "queue:task:t-q"
    assert args[3] == f"queue:{QUEUE_OPERATIONS}:pending"
    assert args[5] == "queued"
    assert args[6] == "zrem"


@pytest.mark.asyncio
async def test_cancel_by_metadata_raced_task_keeps_hash_gets_flag() -> None:
    """The filed race: a queued task that went running is NOT deleted."""
    client, valkey = _make_client(status="queued", evalsha=[b"running", b"__ok__"])
    tasks = {"t-q": _hash_payload(task_id="t-q", status="queued")}
    valkey.hgetall = AsyncMock(side_effect=_get_task_side_effect(tasks))
    valkey.scan_iter = _scan_iter_for(["queue:task:t-q"])

    cancelled = await client.cancel_by_metadata({"database_name": None})

    assert cancelled == 1
    valkey.delete.assert_not_called()
    valkey.set.assert_awaited_once_with("queue:cancel:t-q", "1", ex=300)
    # Fallback guarded write targets the running set.
    args = valkey.evalsha.await_args_list[1].args
    assert args[3] == f"queue:{QUEUE_OPERATIONS}:running"
    assert args[6] == "srem"
