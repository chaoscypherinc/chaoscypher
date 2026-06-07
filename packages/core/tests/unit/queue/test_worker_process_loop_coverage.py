# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage for the ``queue/worker.py`` poll/process loop.

The sibling ``test_worker_helpers_coverage.py`` covers the discrete extracted
helpers (retry/backoff, payload-version gate, health publish, reconcile guards).
This file targets the remaining large uncovered regions — the parts the helper
test deliberately did NOT drive:

- ``_process_task`` — the full single-task dispatch: hash-missing early return,
  missing-handler terminal fail, successful handler dispatch (bare-SREM cleanup
  path AND queue_client atomic-complete + heartbeat path), TimeoutError with a
  retry budget vs. terminal, transient-Exception retry scheduling, transient
  terminal TTL application, and CancelledError marking + re-raise.
- ``_poll_queue`` — empty-pop sleep+continue, retry_after-not-elapsed re-add,
  and the happy path that spawns a processing task; plus the
  semaphore-acquire-timeout continue and the exception branch.
- ``_install_signal_handlers`` — the add_signal_handler path and the
  NotImplementedError fallback to ``signal.signal``.
- ``run`` — end-to-end with an immediate shutdown flag so pollers exit at once.

Construction mirrors the sibling fake-Valkey recording-AsyncMock pattern; helper
definitions are copied locally (no cross-test imports).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.worker import QueueWorker


# ---------------------------------------------------------------------------
# Helpers (copied locally — no sibling-test imports)
# ---------------------------------------------------------------------------


def _make_valkey() -> MagicMock:
    """Build a recording fake async Valkey client for the worker."""
    valkey = MagicMock()
    valkey.hset = AsyncMock(return_value=1)
    valkey.hget = AsyncMock(return_value=b"50")
    valkey.hgetall = AsyncMock(return_value={})
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.zadd = AsyncMock(return_value=1)
    valkey.zpopmax = AsyncMock(return_value=[])
    valkey.setex = AsyncMock(return_value=True)
    valkey.persist = AsyncMock(return_value=True)
    valkey.sadd = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.scard = AsyncMock(return_value=0)
    valkey.zcard = AsyncMock(return_value=0)
    valkey.expire = AsyncMock(return_value=True)
    valkey.delete = AsyncMock(return_value=1)
    return valkey


def _task_hash(
    *,
    operation: str = "test_op",
    attempts: str = "0",
    data: dict[str, Any] | None = None,
) -> dict[bytes, bytes]:
    """Build a decoded-bytes task hash as hgetall would return it."""
    return {
        b"operation": operation.encode(),
        b"data": json.dumps(data or {}).encode(),
        b"metadata": b"{}",
        b"result_ttl": b"3600",
        b"attempts": attempts.encode(),
        b"payload_version": b"1",
        b"priority": b"50",
    }


def _make_worker(
    *,
    queue_client: Any = None,
    handlers: dict[str, dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[QueueWorker, MagicMock]:
    """Construct a QueueWorker over a fake Valkey backend."""
    valkey = _make_valkey()
    worker = QueueWorker(
        client=valkey,
        queues_config={
            QUEUE_OPERATIONS: config or {"concurrency": 1, "max_tries": 3, "timeout": 60},
        },
        handlers=handlers or {QUEUE_OPERATIONS: {"test_op": AsyncMock(return_value="ok")}},
        queue_client=queue_client,
        poll_interval=0.0,
        semaphore_acquire_timeout=0.01,
        poller_error_delay=0.0,
    )
    return worker, valkey


def _config() -> dict[str, Any]:
    """Standard queue config used by _process_task tests."""
    return {"concurrency": 1, "max_tries": 3, "timeout": 60}


# ---------------------------------------------------------------------------
# _process_task — early returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_hash_missing_returns_none() -> None:
    """A missing task hash logs a warning and returns None (bare-SREM cleanup)."""
    worker, valkey = _make_worker()
    valkey.hgetall = AsyncMock(return_value={})  # empty hash

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-miss", QUEUE_OPERATIONS, _config(), sem)

    assert result is None
    # finally: bare SREM cleanup ran.
    valkey.srem.assert_awaited_with(f"queue:{QUEUE_OPERATIONS}:running", "t-miss")


@pytest.mark.asyncio
async def test_process_task_no_handler_marks_terminal() -> None:
    """An unregistered operation marks the task failed terminally and returns None."""
    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {}})  # no handlers
    valkey.hgetall = AsyncMock(return_value=_task_hash(operation="unknown_op"))

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-nohandler", QUEUE_OPERATIONS, _config(), sem)

    assert result is None
    # Terminal-fail bare HSET wrote status=failed with the no-handler error.
    failed = [
        c
        for c in valkey.hset.call_args_list
        if c.kwargs.get("mapping", {}).get("status") == "failed"
    ]
    assert failed
    assert "No handler registered" in failed[0].kwargs["mapping"]["error"]


# ---------------------------------------------------------------------------
# _process_task — successful dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_success_bare_cleanup() -> None:
    """A handler that succeeds returns its result; bare-SREM cleanup runs (no qc)."""
    handler = AsyncMock(return_value="handler-result")
    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {"test_op": handler}})
    valkey.hgetall = AsyncMock(return_value=_task_hash())

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-ok", QUEUE_OPERATIONS, _config(), sem)

    assert result == "handler-result"
    handler.assert_awaited_once()
    # attempts incremented + status=running written + SREM cleanup.
    valkey.hincrby.assert_any_await("queue:task:t-ok", "attempts", 1)
    valkey.srem.assert_awaited_with(f"queue:{QUEUE_OPERATIONS}:running", "t-ok")
    # Semaphore released in finally.
    assert sem.locked() is False


@pytest.mark.asyncio
async def test_process_task_success_with_queue_client_atomic_path() -> None:
    """With a queue_client, the heartbeat + atomic-complete path is exercised."""
    handler = AsyncMock(return_value="ok")
    qc = MagicMock()
    qc.set_heartbeat = AsyncMock(return_value=None)
    qc.refresh_heartbeat = AsyncMock(return_value=None)
    qc.complete_task_atomic = AsyncMock(return_value=None)
    qc.failed_result_ttl = 1209600
    qc.get_transient_retry_policy = MagicMock(return_value=True)

    worker, valkey = _make_worker(
        handlers={QUEUE_OPERATIONS: {"test_op": handler}}, queue_client=qc
    )
    # Heartbeat refresh interval long enough that no refresh fires during the test.
    worker._heartbeat_refresh_interval_seconds = 60
    valkey.hgetall = AsyncMock(return_value=_task_hash())

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-qc", QUEUE_OPERATIONS, _config(), sem)

    assert result == "ok"
    qc.set_heartbeat.assert_awaited_once()
    qc.complete_task_atomic.assert_awaited_once_with(QUEUE_OPERATIONS, "t-qc")
    # bare SREM NOT used when qc present.
    valkey.srem.assert_not_awaited()


# ---------------------------------------------------------------------------
# _process_task — timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_timeout_schedules_retry() -> None:
    """A handler timeout with retry budget left schedules a retry after release."""

    async def _slow(*_a: Any, **_k: Any) -> None:
        await asyncio.sleep(10)

    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {"test_op": _slow}})
    valkey.hgetall = AsyncMock(return_value=_task_hash(attempts="0"))

    # timeout=0 forces an immediate TimeoutError from asyncio.wait_for.
    cfg = {"concurrency": 1, "max_tries": 3, "timeout": 0}
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-timeout", QUEUE_OPERATIONS, cfg, sem)

    assert result is None
    # will_retry True -> failed_fields HSET (not terminal mark) then retry re-add.
    failed = [
        c
        for c in valkey.hset.call_args_list
        if c.kwargs.get("mapping", {}).get("error_type") == "transient"
    ]
    assert failed
    # _retry_task re-added the task to pending.
    assert valkey.zadd.await_count >= 1


@pytest.mark.asyncio
async def test_process_task_timeout_terminal_on_last_attempt() -> None:
    """A timeout on the final attempt is terminal (no retry, no re-add)."""

    async def _slow(*_a: Any, **_k: Any) -> None:
        await asyncio.sleep(10)

    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {"test_op": _slow}})
    # attempts=2, max_tries=3 -> attempts+1 == max_tries -> terminal.
    valkey.hgetall = AsyncMock(return_value=_task_hash(attempts="2"))

    cfg = {"concurrency": 1, "max_tries": 3, "timeout": 0}
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-timeout-last", QUEUE_OPERATIONS, cfg, sem)

    assert result is None
    # No retry re-add to pending (only no zadd from _retry_task).
    assert valkey.zadd.await_count == 0


# ---------------------------------------------------------------------------
# _process_task — exception (transient/permanent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_transient_exception_retries() -> None:
    """A transient handler exception with budget left schedules a retry."""

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("transient network blip")

    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {"test_op": _boom}})
    valkey.hgetall = AsyncMock(return_value=_task_hash(attempts="0"))

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-transient", QUEUE_OPERATIONS, _config(), sem)

    assert result is None
    # _retry_task re-added the task to pending.
    assert valkey.zadd.await_count >= 1


@pytest.mark.asyncio
async def test_process_task_transient_terminal_applies_ttl() -> None:
    """A transient exception with NO budget left applies the dead-letter TTL (qc path)."""

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("transient blip")

    qc = MagicMock()
    qc.set_heartbeat = AsyncMock(return_value=None)
    qc.refresh_heartbeat = AsyncMock(return_value=None)
    qc.complete_task_atomic = AsyncMock(return_value=None)
    qc.failed_result_ttl = 1209600
    qc.get_transient_retry_policy = MagicMock(return_value=True)

    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {"test_op": _boom}}, queue_client=qc)
    worker._heartbeat_refresh_interval_seconds = 60
    # attempts=2, max_tries=3 -> no retry budget -> terminal transient.
    valkey.hgetall = AsyncMock(return_value=_task_hash(attempts="2"))

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    result = await worker._process_task("t-term", QUEUE_OPERATIONS, _config(), sem)

    assert result is None
    # Dead-letter retention TTL applied on the task hash.
    valkey.expire.assert_any_await("queue:task:t-term", qc.failed_result_ttl)


@pytest.mark.asyncio
async def test_process_task_cancelled_marks_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CancelledError escaping handler dispatch marks status=cancelled and re-raises.

    ``_execute_handler`` swallows a handler-raised CancelledError into a
    cancelled-result return, so to drive the worker's OWN cancelled branch we
    patch the module-level ``_execute_handler`` to let the CancelledError
    propagate (mirrors a task cancelled by the drain/shutdown path).
    """
    import chaoscypher_core.queue.worker as worker_mod

    async def _raise_cancel(*_a: Any, **_k: Any) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_mod, "_execute_handler", _raise_cancel)

    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {"test_op": AsyncMock()}})
    valkey.hgetall = AsyncMock(return_value=_task_hash())

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    with pytest.raises(asyncio.CancelledError):
        await worker._process_task("t-cancel", QUEUE_OPERATIONS, _config(), sem)

    cancelled = [
        c
        for c in valkey.hset.call_args_list
        if c.kwargs.get("mapping", {}).get("status") == "cancelled"
    ]
    assert cancelled


# ---------------------------------------------------------------------------
# _poll_queue — single iterations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_queue_empty_pop_sleeps_and_exits() -> None:
    """An empty ZPOPMAX releases the slot, sleeps, then the loop exits on _running."""
    worker, valkey = _make_worker()
    valkey.zpopmax = AsyncMock(return_value=[])

    # Flip _running to False after the first sleep so the loop exits.
    original_sleep = asyncio.sleep

    async def _sleep_then_stop(_secs: float) -> None:
        worker._running = False
        await original_sleep(0)

    import chaoscypher_core.queue.worker as worker_mod

    worker._running = True
    orig = worker_mod.asyncio.sleep
    worker_mod.asyncio.sleep = _sleep_then_stop
    try:
        await worker._poll_queue(QUEUE_OPERATIONS, _config())
    finally:
        worker_mod.asyncio.sleep = orig

    valkey.zpopmax.assert_awaited()


@pytest.mark.asyncio
async def test_poll_queue_retry_after_not_elapsed_readds() -> None:
    """A popped task whose retry_after is in the future is re-added and skipped."""
    import time

    worker, valkey = _make_worker()
    valkey.zpopmax = AsyncMock(return_value=[(b"t-future", 50.0)])
    # retry_after far in the future.
    valkey.hget = AsyncMock(return_value=str(time.time() + 10_000).encode())

    import chaoscypher_core.queue.worker as worker_mod

    async def _sleep_then_stop(_secs: float) -> None:
        worker._running = False

    worker._running = True
    orig = worker_mod.asyncio.sleep
    worker_mod.asyncio.sleep = _sleep_then_stop
    try:
        await worker._poll_queue(QUEUE_OPERATIONS, _config())
    finally:
        worker_mod.asyncio.sleep = orig

    # Task re-added to pending with its original score; not processed.
    readd = [
        c
        for c in valkey.zadd.call_args_list
        if c.args and c.args[0] == f"queue:{QUEUE_OPERATIONS}:pending"
    ]
    assert readd


@pytest.mark.asyncio
async def test_poll_queue_spawns_processing_task() -> None:
    """A ready popped task spawns a processing task and registers it as active."""
    handler = AsyncMock(return_value="ok")
    worker, valkey = _make_worker(handlers={QUEUE_OPERATIONS: {"test_op": handler}})
    valkey.zpopmax = AsyncMock(return_value=[(b"t-ready", 50.0)])
    valkey.hget = AsyncMock(return_value=None)  # no retry_after
    valkey.hgetall = AsyncMock(return_value=_task_hash())

    import chaoscypher_core.queue.worker as worker_mod

    spawned: list[str] = []

    async def _stop_after_spawn(_secs: float) -> None:
        worker._running = False

    # Stop the loop right after the first poll iteration creates a task.
    worker._running = True

    async def _fake_process(task_id: str, *_a: Any, **_k: Any) -> None:
        spawned.append(task_id)
        worker._running = False

    worker._process_task = _fake_process  # type: ignore[method-assign]

    orig = worker_mod.asyncio.sleep
    worker_mod.asyncio.sleep = _stop_after_spawn
    try:
        await worker._poll_queue(QUEUE_OPERATIONS, _config())
        # Allow the spawned task to run.
        await asyncio.sleep(0)
    finally:
        worker_mod.asyncio.sleep = orig

    assert spawned == ["t-ready"]


@pytest.mark.asyncio
async def test_poll_queue_exception_branch_logs_and_delays() -> None:
    """An exception inside the poll body is logged and the loop pauses then exits."""
    worker, valkey = _make_worker()
    # zpopmax raises -> exception branch.
    valkey.zpopmax = AsyncMock(side_effect=RuntimeError("valkey error"))

    import chaoscypher_core.queue.worker as worker_mod

    async def _stop(_secs: float) -> None:
        worker._running = False

    worker._running = True
    orig = worker_mod.asyncio.sleep
    worker_mod.asyncio.sleep = _stop
    try:
        await worker._poll_queue(QUEUE_OPERATIONS, _config())
    finally:
        worker_mod.asyncio.sleep = orig

    valkey.zpopmax.assert_awaited()


# ---------------------------------------------------------------------------
# _install_signal_handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_signal_handlers_uses_loop_handler() -> None:
    """When the loop supports add_signal_handler, it is used for SIGTERM/SIGINT."""
    worker, _ = _make_worker()
    loop = asyncio.get_running_loop()
    registered: list[Any] = []

    def _add(sig: Any, _cb: Any) -> None:
        registered.append(sig)

    orig = loop.add_signal_handler
    loop.add_signal_handler = _add  # type: ignore[method-assign]
    try:
        worker._install_signal_handlers()
    finally:
        loop.add_signal_handler = orig  # type: ignore[method-assign]

    import signal

    assert signal.SIGTERM in registered
    assert signal.SIGINT in registered


@pytest.mark.asyncio
async def test_install_signal_handlers_falls_back_to_signal_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When add_signal_handler raises NotImplementedError, fall back to signal.signal."""
    import signal as signal_mod

    worker, _ = _make_worker()
    loop = asyncio.get_running_loop()

    def _raise(_sig: Any, _cb: Any) -> None:
        raise NotImplementedError

    fallback: list[Any] = []

    def _fake_signal(sig: Any, _handler: Any) -> None:
        fallback.append(sig)

    orig = loop.add_signal_handler
    loop.add_signal_handler = _raise  # type: ignore[method-assign]
    monkeypatch.setattr(signal_mod, "signal", _fake_signal)
    try:
        worker._install_signal_handlers()
    finally:
        loop.add_signal_handler = orig  # type: ignore[method-assign]

    assert signal_mod.SIGTERM in fallback
    assert signal_mod.SIGINT in fallback


# ---------------------------------------------------------------------------
# run — end-to-end with immediate shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_starts_and_stops_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """run() installs handlers, starts pollers/background tasks, and stops on flag."""
    worker, valkey = _make_worker()

    # Neutralise signal handler install (no real OS handlers in the test loop).
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda: None)

    # Make the poller a no-op that exits immediately.
    async def _noop_poll(_q: str, _c: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(worker, "_poll_queue", _noop_poll)

    # Health publisher exits immediately.
    async def _noop_health() -> None:
        return None

    monkeypatch.setattr(worker, "_publish_health", _noop_health)

    # Flip _running off so the gather over pollers returns at once.
    worker._running = False

    await worker.run()

    # Health/running keys deleted on shutdown.
    valkey.delete.assert_any_await(f"queue:{QUEUE_OPERATIONS}:health")
    valkey.delete.assert_any_await(f"queue:{QUEUE_OPERATIONS}:running")
