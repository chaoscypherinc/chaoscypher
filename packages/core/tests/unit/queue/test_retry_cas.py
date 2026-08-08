# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``retry_task`` claims the original via CAS before enqueueing.

Pins the 2026-07-19 hunter finding's fix (verdict 2026-07-23): the old
check-then-enqueue let a double-click enqueue duplicate work (duplicate
LLM cost). Now a guarded ``failed -> retried`` write gates the enqueue —
only the caller whose CAS wins proceeds; the loser returns None. On an
enqueue failure the claim is reverted best-effort so the task stays
retryable.
"""

from __future__ import annotations

import json
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


def _failed_payload() -> dict[str, str]:
    return {
        "task_id": "t-f",
        "queue": QUEUE_OPERATIONS,
        "operation": "test_op",
        "status": "failed",
        "priority": "50",
        "created_at": "2026-05-23T00:00:00Z",
        "data": json.dumps({"k": "v"}),
        "metadata": json.dumps({"database_name": "default"}),
        "result_ttl": "3600",
        "attempts": "2",
        "payload_version": "1",
    }


def _make_client(evalsha: object | list[object] = b"__ok__") -> tuple[QueueClient, MagicMock]:
    client = _bare_client()
    valkey = MagicMock()
    valkey.hgetall = AsyncMock(return_value=_failed_payload())
    valkey.hset = AsyncMock(return_value=1)
    valkey.script_load = AsyncMock(return_value="sha-abc")
    if isinstance(evalsha, list):
        valkey.evalsha = AsyncMock(side_effect=evalsha)
    else:
        valkey.evalsha = AsyncMock(return_value=evalsha)
    client.client = valkey
    client.enqueue_task = AsyncMock(return_value="t-new")  # type: ignore[method-assign]
    return client, valkey


@pytest.mark.asyncio
async def test_retry_claims_via_cas_then_enqueues_and_links() -> None:
    """Winning the failed->retried CAS enqueues and records retried_to."""
    client, valkey = _make_client()

    new_id = await client.retry_task("t-f")

    assert new_id == "t-new"
    args = valkey.evalsha.await_args.args
    assert args[2] == "queue:task:t-f"
    assert args[5] == "failed"  # allowed_from
    pairs = dict(zip(args[8::2], args[9::2], strict=True))
    assert pairs["status"] == "retried"
    assert "retried_at" in pairs
    client.enqueue_task.assert_awaited_once()  # type: ignore[attr-defined]
    kwargs = client.enqueue_task.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["metadata"]["retried_from"] == "t-f"
    valkey.hset.assert_awaited_once_with("queue:task:t-f", mapping={"retried_to": "t-new"})


@pytest.mark.asyncio
async def test_retry_double_click_enqueues_once() -> None:
    """A concurrent retry that lost the CAS returns None without enqueueing."""
    client, _valkey = _make_client(evalsha=b"retried")

    assert await client.retry_task("t-f") is None
    client.enqueue_task.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_retry_non_failed_status_raises() -> None:
    """Pre-read status check preserves the ValueError contract."""
    client, valkey = _make_client()
    payload = _failed_payload()
    payload["status"] = "running"
    valkey.hgetall = AsyncMock(return_value=payload)

    with pytest.raises(ValueError, match="must be 'failed'"):
        await client.retry_task("t-f")
    client.enqueue_task.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_retry_enqueue_failure_reverts_claim() -> None:
    """An enqueue failure reverts retried->failed so the task stays retryable."""
    client, valkey = _make_client(evalsha=[b"__ok__", b"__ok__"])
    client.enqueue_task = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        await client.retry_task("t-f")

    assert valkey.evalsha.await_count == 2
    revert_args = valkey.evalsha.await_args_list[1].args
    pairs = dict(zip(revert_args[8::2], revert_args[9::2], strict=True))
    assert pairs == {"status": "failed"}
    assert revert_args[5] == "retried"  # allowed_from the claim state
