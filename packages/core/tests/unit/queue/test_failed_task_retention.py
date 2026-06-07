# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for queue dead-letter retention (failed-task TTL).

Covers the P2 reliability item from the 2026-05-18 production-launch audit:
failed task hashes used to live only as long as ``result_ttl=3600`` (1 hour)
before disappearing, so operators who didn't check within the window lost
post-mortem data. The fix applies ``TimeoutSettings.failed_result_ttl``
(default 14 days = 1_209_600s) as an ``EXPIRE`` on the ``queue:task:{id}``
hash whenever the task reaches a terminal ``status=failed``.

Tested fail sites:
- ``QueueClient.mark_task_failed_terminal`` — the canonical helper.
- Worker permanent path via ``_execute_handler`` (permanent error branch).
- Worker timeout path on the last attempt (no retry budget left).
- Worker no-handler bail-out.
- Reconciler abandonment when retry policy denies retry.
- ``_retry_task`` PERSIST defensive: a re-queued task gets its TTL cleared
  so a long EXPIRE doesn't auto-delete a healthy in-flight task.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.client import QueueClient
from chaoscypher_core.queue.service import _execute_handler


# Use a short retention override in tests so assertions are unambiguous.
# Production default lives in ``TimeoutSettings.failed_result_ttl`` (14 days).
_TEST_FAILED_TTL = 1_209_600  # 14 days, matches default — explicit for readability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_queue_client(failed_ttl: int = _TEST_FAILED_TTL) -> tuple[QueueClient, MagicMock]:
    """Build a QueueClient with a recording fake Valkey backend."""
    client = QueueClient.__new__(QueueClient)
    client._connected = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 3600
    client._llm_result_ttl = 3600
    client._failed_result_ttl = failed_ttl
    client._transient_retry_policy = {}
    client._handlers = {}
    client._retry_policy = {}
    client._queues = set()

    valkey = MagicMock()
    valkey.hset = AsyncMock(return_value=1)
    valkey.expire = AsyncMock(return_value=True)
    valkey.delete = AsyncMock(return_value=1)
    valkey.persist = AsyncMock(return_value=True)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hgetall = AsyncMock(return_value={})
    valkey.zadd = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=1)
    client.client = valkey
    return client, valkey


# ---------------------------------------------------------------------------
# QueueClient.mark_task_failed_terminal — canonical contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_task_failed_terminal_writes_status_and_applies_ttl() -> None:
    """The canonical helper HSETs the failed fields AND applies the retention TTL."""
    client, valkey = _make_queue_client()

    await client.mark_task_failed_terminal(
        "task-abc",
        {
            "status": "failed",
            "error": "boom",
            "error_type": "permanent",
            "completed_at": "2026-05-23T12:00:00Z",
        },
    )

    valkey.hset.assert_awaited_once()
    args, kwargs = valkey.hset.call_args
    assert args[0] == "queue:task:task-abc"
    assert kwargs["mapping"]["status"] == "failed"
    assert kwargs["mapping"]["error"] == "boom"

    valkey.expire.assert_awaited_once_with("queue:task:task-abc", _TEST_FAILED_TTL)


@pytest.mark.asyncio
async def test_mark_task_failed_terminal_respects_configured_ttl() -> None:
    """A non-default ``failed_result_ttl`` flows through the helper."""
    custom_ttl = 7 * 86_400  # 7 days
    client, valkey = _make_queue_client(failed_ttl=custom_ttl)

    await client.mark_task_failed_terminal("task-xyz", {"status": "failed"})

    valkey.expire.assert_awaited_once_with("queue:task:task-xyz", custom_ttl)


@pytest.mark.asyncio
async def test_failed_result_ttl_property_exposes_configured_value() -> None:
    """The read-only property surfaces the configured retention."""
    client, _ = _make_queue_client(failed_ttl=42)
    assert client.failed_result_ttl == 42


# ---------------------------------------------------------------------------
# _execute_handler — permanent error branch applies TTL
# ---------------------------------------------------------------------------


def _make_handler_client() -> MagicMock:
    """Minimal fake Valkey for ``_execute_handler`` tests."""
    client = MagicMock()
    client.hset = AsyncMock(return_value=1)
    client.setex = AsyncMock(return_value=True)
    client.expire = AsyncMock(return_value=True)
    return client


@pytest.mark.asyncio
async def test_execute_handler_applies_failed_ttl_on_permanent_failure() -> None:
    """Permanent failures get the dead-letter retention TTL on the task hash."""

    async def boom_handler(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("bad input")  # ValueError → classified as permanent

    valkey = _make_handler_client()
    result = await _execute_handler(
        handler=boom_handler,
        task_id="t-perm",
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={},
        metadata={},
        result_ttl=60,
        client=valkey,
        failed_result_ttl=_TEST_FAILED_TTL,
    )

    # Returned a failure envelope, did not raise.
    assert result["status"] == "failed"
    assert result["error_type"] == "permanent"

    # Hash got status=failed AND retention TTL.
    valkey.hset.assert_awaited_once()
    mapping = valkey.hset.call_args.kwargs["mapping"]
    assert mapping["status"] == "failed"
    assert mapping["error_type"] == "permanent"

    valkey.expire.assert_awaited_once_with("queue:task:t-perm", _TEST_FAILED_TTL)


@pytest.mark.asyncio
async def test_execute_handler_does_not_apply_ttl_on_transient_failure() -> None:
    """Transient failures must NOT apply the retention TTL — the worker may retry."""

    async def flaky_handler(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("temporary blip")  # transient

    valkey = _make_handler_client()
    with pytest.raises(ConnectionError):
        await _execute_handler(
            handler=flaky_handler,
            task_id="t-trans",
            queue=QUEUE_OPERATIONS,
            operation="test_op",
            data={},
            metadata={},
            result_ttl=60,
            client=valkey,
            failed_result_ttl=_TEST_FAILED_TTL,
        )

    # Status was written (so the operator can see it), but no EXPIRE —
    # a retry may bring the task back to status=queued and we don't want
    # a long EXPIRE to silently delete a healthy task hash.
    valkey.hset.assert_awaited()
    valkey.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_handler_legacy_no_failed_ttl_still_writes_status() -> None:
    """Pre-retention callers (failed_result_ttl=None) keep working without EXPIRE."""

    async def boom_handler(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("bad input")

    valkey = _make_handler_client()
    await _execute_handler(
        handler=boom_handler,
        task_id="t-legacy",
        queue=QUEUE_OPERATIONS,
        operation="test_op",
        data={},
        metadata={},
        result_ttl=60,
        client=valkey,
        failed_result_ttl=None,
    )

    valkey.hset.assert_awaited()
    valkey.expire.assert_not_awaited()


# ---------------------------------------------------------------------------
# Worker fail sites — TTL applied when terminal
# ---------------------------------------------------------------------------


def _build_worker_with_queue_client(
    handler: Any,
    hash_payload: dict[str, str],
    *,
    max_tries: int = 3,
    timeout: int = 60,
) -> tuple[Any, MagicMock, QueueClient, MagicMock]:
    """Construct a QueueWorker WITH a real-ish QueueClient injected.

    Returns ``(worker, raw_valkey, queue_client, queue_client_valkey)``.
    The worker's ``self.client`` is the raw Valkey mock; the QueueClient's
    ``client.client`` is the same mock so EXPIRE calls flow through to one
    place we can assert against.
    """
    from chaoscypher_core.queue.worker import QueueWorker

    queue_client, qc_valkey = _make_queue_client()
    # Use the same valkey mock for both — that way HSET/EXPIRE assertions
    # are unambiguous regardless of which path issued the call.
    queue_client.complete_task_atomic = AsyncMock(return_value=None)
    queue_client.set_heartbeat = AsyncMock(return_value=None)
    queue_client.refresh_heartbeat = AsyncMock(return_value=None)

    valkey = qc_valkey
    valkey.sadd = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hgetall = AsyncMock(return_value=hash_payload)
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.hget = AsyncMock(return_value=b"50")
    valkey.zadd = AsyncMock(return_value=1)

    worker = QueueWorker(
        client=valkey,
        queues_config={
            QUEUE_OPERATIONS: {"concurrency": 1, "max_tries": max_tries, "timeout": timeout},
        },
        handlers={QUEUE_OPERATIONS: {"test_op": handler}},
        queue_client=queue_client,
    )
    return worker, valkey, queue_client, qc_valkey


def _make_hash_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "task_id": "t-default",
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


@pytest.mark.asyncio
async def test_worker_no_handler_marks_terminal_with_ttl() -> None:
    """When no handler is registered, the worker marks the hash failed AND applies the TTL."""
    hash_payload = _make_hash_payload(task_id="t-nohandler", operation="missing_op")

    # No handler registered for "missing_op" — the worker bails out.
    worker, valkey, _qc, _qc_v = _build_worker_with_queue_client(
        handler=None, hash_payload=hash_payload
    )
    # Override the handler map so "missing_op" has no registered callable.
    worker.handlers = {QUEUE_OPERATIONS: {}}

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-nohandler",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    # The mark_task_failed_terminal helper drives HSET + EXPIRE.
    failed_calls = [
        call
        for call in valkey.hset.call_args_list
        if call.kwargs.get("mapping", {}).get("status") == "failed"
    ]
    assert failed_calls, "Worker did not mark task as failed"
    expire_calls = [
        call
        for call in valkey.expire.call_args_list
        if call.args and call.args[0] == "queue:task:t-nohandler"
    ]
    assert expire_calls, "Worker did not apply dead-letter retention TTL"
    assert expire_calls[-1].args[1] == _TEST_FAILED_TTL


@pytest.mark.asyncio
async def test_worker_timeout_last_attempt_applies_retention_ttl() -> None:
    """A timeout on the final attempt is terminal — TTL is applied."""

    async def slow_handler(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(10)  # will be cancelled by wait_for

    # attempts=2, max_tries=3 → ``attempts + 1`` (after hincrby) == max_tries,
    # so this IS the last attempt and no retry will be scheduled.
    hash_payload = _make_hash_payload(task_id="t-timeout-last", attempts="2")

    worker, valkey, _qc, _qc_v = _build_worker_with_queue_client(
        handler=slow_handler,
        hash_payload=hash_payload,
        max_tries=3,
        timeout=0,  # immediate timeout via asyncio.wait_for
    )

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-timeout-last",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    # Failed status was written and the retention TTL was applied.
    expire_calls = [
        call
        for call in valkey.expire.call_args_list
        if call.args and call.args[0] == "queue:task:t-timeout-last"
    ]
    assert expire_calls, "Terminal timeout did not apply dead-letter retention TTL"
    assert expire_calls[-1].args[1] == _TEST_FAILED_TTL


@pytest.mark.asyncio
async def test_worker_timeout_non_terminal_skips_retention_ttl() -> None:
    """A timeout with retry budget remaining MUST NOT apply the retention TTL."""

    async def slow_handler(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(10)

    # attempts=0, max_tries=3 → retry will be scheduled, this is NOT terminal.
    hash_payload = _make_hash_payload(task_id="t-timeout-retry", attempts="0")

    worker, valkey, _qc, _qc_v = _build_worker_with_queue_client(
        handler=slow_handler,
        hash_payload=hash_payload,
        max_tries=3,
        timeout=0,
    )

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-timeout-retry",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    # Status=failed was written (for visibility) but no EXPIRE on the task
    # hash with the retention TTL — the retry would otherwise auto-delete
    # a healthy in-flight task. ``persist`` may be called by ``_retry_task``
    # as a defensive measure; that's fine, what we forbid is EXPIRE with the
    # 14-day TTL.
    bad_expire_calls = [
        call
        for call in valkey.expire.call_args_list
        if call.args
        and call.args[0] == "queue:task:t-timeout-retry"
        and len(call.args) > 1
        and call.args[1] == _TEST_FAILED_TTL
    ]
    assert not bad_expire_calls, (
        "Worker applied retention TTL on a non-terminal timeout — would "
        "auto-delete the task before retry can reset status to queued"
    )


@pytest.mark.asyncio
async def test_worker_timeout_policy_disabled_marks_terminal_without_retry() -> None:
    """Handlers with their own retry budget do not get queue-level timeout retries."""

    async def slow_handler(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(10)

    hash_payload = _make_hash_payload(task_id="t-timeout-handler-managed", attempts="0")
    worker, valkey, queue_client, _qc_v = _build_worker_with_queue_client(
        handler=slow_handler,
        hash_payload=hash_payload,
        max_tries=3,
        timeout=0,
    )
    queue_client._transient_retry_policy = {QUEUE_OPERATIONS: {"test_op": False}}

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-timeout-handler-managed",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    valkey.zadd.assert_not_awaited()
    expire_calls = [
        call
        for call in valkey.expire.call_args_list
        if call.args and call.args[0] == "queue:task:t-timeout-handler-managed"
    ]
    assert expire_calls, "Handler-managed timeout did not become a terminal failure"
    assert expire_calls[-1].args[1] == _TEST_FAILED_TTL


@pytest.mark.asyncio
async def test_worker_transient_policy_disabled_marks_terminal_without_retry() -> None:
    """A handler-managed transient exception is not also retried by the queue."""

    async def flaky_handler(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("temporary provider outage")

    hash_payload = _make_hash_payload(task_id="t-transient-handler-managed", attempts="0")
    worker, valkey, queue_client, _qc_v = _build_worker_with_queue_client(
        handler=flaky_handler,
        hash_payload=hash_payload,
        max_tries=3,
        timeout=60,
    )
    queue_client._transient_retry_policy = {QUEUE_OPERATIONS: {"test_op": False}}

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-transient-handler-managed",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    valkey.zadd.assert_not_awaited()
    expire_calls = [
        call
        for call in valkey.expire.call_args_list
        if call.args and call.args[0] == "queue:task:t-transient-handler-managed"
    ]
    assert expire_calls, "Handler-managed transient failure did not get terminal TTL"
    assert expire_calls[-1].args[1] == _TEST_FAILED_TTL


@pytest.mark.asyncio
async def test_worker_permanent_handler_failure_applies_retention_ttl() -> None:
    """A permanent error from the handler reaches _execute_handler and gets the TTL."""

    async def perm_handler(data: dict[str, Any], **kwargs: Any) -> Any:
        raise ValueError("invalid input")  # permanent

    hash_payload = _make_hash_payload(task_id="t-perm-handler")
    worker, valkey, _qc, _qc_v = _build_worker_with_queue_client(
        handler=perm_handler, hash_payload=hash_payload
    )

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    await worker._process_task(
        "t-perm-handler",
        QUEUE_OPERATIONS,
        worker.queues_config[QUEUE_OPERATIONS],
        sem,
    )

    expire_calls = [
        call
        for call in valkey.expire.call_args_list
        if call.args and call.args[0] == "queue:task:t-perm-handler"
    ]
    assert expire_calls, "Permanent handler failure did not apply retention TTL"
    assert expire_calls[-1].args[1] == _TEST_FAILED_TTL


@pytest.mark.asyncio
async def test_retry_task_persists_ttl_for_safety() -> None:
    """``_retry_task`` PERSISTs the task hash so a stray TTL can't delete a re-queued task."""
    from chaoscypher_core.queue.worker import QueueWorker

    valkey = MagicMock()
    valkey.hset = AsyncMock(return_value=1)
    valkey.hget = AsyncMock(return_value=b"50")
    valkey.zadd = AsyncMock(return_value=1)
    valkey.persist = AsyncMock(return_value=True)

    worker = QueueWorker(
        client=valkey,
        queues_config={QUEUE_OPERATIONS: {"concurrency": 1, "max_tries": 3, "timeout": 60}},
        handlers={QUEUE_OPERATIONS: {"test_op": lambda *a, **k: None}},
    )

    await worker._retry_task("t-retry", QUEUE_OPERATIONS, attempt=1, max_tries=3)

    valkey.persist.assert_awaited_once_with("queue:task:t-retry")


# ---------------------------------------------------------------------------
# Settings field default sanity check
# ---------------------------------------------------------------------------


def test_failed_result_ttl_default_is_14_days() -> None:
    """``TimeoutSettings.failed_result_ttl`` defaults to 14 days (matches backup retention)."""
    from chaoscypher_core.app_config import TimeoutSettings

    settings = TimeoutSettings()
    assert settings.failed_result_ttl == 14 * 86_400
    assert settings.failed_result_ttl == 1_209_600  # documented in the commit message
