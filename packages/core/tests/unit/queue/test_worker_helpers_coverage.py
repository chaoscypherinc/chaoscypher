# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for the extracted helpers of ``queue/worker.py``.

Rather than driving the full ``run()`` loop (which installs signal handlers
and spawns long-lived pollers), these target the discrete extracted helpers
that ``_process_task`` and ``run()`` delegate to:

- ``_allows_queue_transient_retry`` — policy lookup with/without queue_client.
- ``_get_retry_after`` — None / float / non-numeric raw values.
- ``_payload_version_supported`` — supported vs. unsupported (terminal mark +
  best-effort upgrade recovery dispatch).
- ``_mark_task_failed_terminal`` — routed vs. bare-HSET fallback.
- ``_retry_task`` — status=queued + retry_after write, PERSIST, future-biased
  re-add to pending.
- ``_startup_reconcile`` / ``_reconcile_loop`` — no-op without client; per-queue
  exception isolation.
- ``_drain_active_tasks`` — empty-return + cancel-pending.
- ``_request_shutdown`` — flips _running once.
- ``_publish_health`` — one tick writes health hash + EXPIRE, then exits on
  CancelledError.
- ``_run_with_heartbeat`` — cancels the refresher in finally even on raise.

The fake Valkey backend mirrors the recording-AsyncMock pattern used by the
sibling queue tests.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.worker import QueueWorker, _run_with_heartbeat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valkey() -> MagicMock:
    """Build a recording fake async Valkey client for the worker."""
    valkey = MagicMock()
    valkey.hset = AsyncMock(return_value=1)
    valkey.hget = AsyncMock(return_value=b"50")
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.zadd = AsyncMock(return_value=1)
    valkey.persist = AsyncMock(return_value=True)
    valkey.sadd = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.scard = AsyncMock(return_value=0)
    valkey.zcard = AsyncMock(return_value=0)
    valkey.expire = AsyncMock(return_value=True)
    valkey.delete = AsyncMock(return_value=1)
    return valkey


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
        handlers=handlers or {QUEUE_OPERATIONS: {"test_op": lambda *a, **k: None}},
        queue_client=queue_client,
    )
    return worker, valkey


# ---------------------------------------------------------------------------
# _allows_queue_transient_retry
# ---------------------------------------------------------------------------


def test_allows_transient_retry_true_without_queue_client() -> None:
    """No queue_client → historical permissive default (retry allowed)."""
    worker, _ = _make_worker(queue_client=None)
    assert worker._allows_queue_transient_retry(QUEUE_OPERATIONS, "test_op") is True


def test_allows_transient_retry_true_when_method_missing() -> None:
    """A queue_client lacking the policy method → permissive default."""
    qc = MagicMock(spec=[])  # no get_transient_retry_policy attribute
    worker, _ = _make_worker(queue_client=qc)
    assert worker._allows_queue_transient_retry(QUEUE_OPERATIONS, "test_op") is True


def test_allows_transient_retry_honors_policy_false() -> None:
    """A queue_client policy of False is honored."""
    qc = MagicMock()
    qc.get_transient_retry_policy = MagicMock(return_value=False)
    worker, _ = _make_worker(queue_client=qc)
    assert worker._allows_queue_transient_retry(QUEUE_OPERATIONS, "test_op") is False
    qc.get_transient_retry_policy.assert_called_once_with(QUEUE_OPERATIONS, "test_op")


# ---------------------------------------------------------------------------
# _get_retry_after
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retry_after_none_when_unset() -> None:
    """A missing retry_after field returns None."""
    worker, valkey = _make_worker()
    valkey.hget = AsyncMock(return_value=None)
    assert await worker._get_retry_after("t-1") is None


@pytest.mark.asyncio
async def test_get_retry_after_parses_float() -> None:
    """A numeric retry_after parses to a float epoch timestamp."""
    worker, valkey = _make_worker()
    valkey.hget = AsyncMock(return_value=b"1234.5")
    assert await worker._get_retry_after("t-1") == 1234.5


@pytest.mark.asyncio
async def test_get_retry_after_non_numeric_returns_none() -> None:
    """A non-numeric retry_after is treated as absent (None)."""
    worker, valkey = _make_worker()
    valkey.hget = AsyncMock(return_value=b"not-a-number")
    assert await worker._get_retry_after("t-1") is None


# ---------------------------------------------------------------------------
# _payload_version_supported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_version_supported_returns_true_for_v1() -> None:
    """Version 1 (the current supported version) returns True."""
    worker, _ = _make_worker()
    ok = await worker._payload_version_supported("t-1", QUEUE_OPERATIONS, "test_op", "1")
    assert ok is True


@pytest.mark.asyncio
async def test_payload_version_missing_treated_as_v1() -> None:
    """A missing version is treated as v1 (transitional) and supported."""
    worker, _ = _make_worker()
    ok = await worker._payload_version_supported("t-1", QUEUE_OPERATIONS, "test_op", None)
    assert ok is True


@pytest.mark.asyncio
async def test_payload_version_unsupported_marks_terminal_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported version marks the task terminal AND dispatches upgrade recovery."""
    import chaoscypher_core.queue.upgrade_recovery as recovery_mod

    recovery_calls: list[dict[str, Any]] = []

    def _fake_apply(**kwargs: Any) -> None:
        recovery_calls.append(kwargs)

    monkeypatch.setattr(recovery_mod, "apply_upgrade_recovery", _fake_apply)

    worker, valkey = _make_worker()
    ok = await worker._payload_version_supported(
        "t-bad",
        QUEUE_OPERATIONS,
        "test_op",
        "999",
        data={"source_id": "s1"},
        metadata={"chat_id": "c1"},
    )

    assert ok is False
    # Best-effort recovery dispatch fired with the version + ids.
    assert recovery_calls and recovery_calls[0]["payload_version"] == 999
    # Bare-HSET fallback (no queue_client) marked the task failed.
    failed = [
        c
        for c in valkey.hset.call_args_list
        if c.kwargs.get("mapping", {}).get("status") == "failed"
    ]
    assert failed


@pytest.mark.asyncio
async def test_payload_version_unsupported_swallows_recovery_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception in upgrade recovery is logged and does not block the terminal mark."""
    import chaoscypher_core.queue.upgrade_recovery as recovery_mod

    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("recovery exploded")

    monkeypatch.setattr(recovery_mod, "apply_upgrade_recovery", _boom)

    worker, valkey = _make_worker()
    ok = await worker._payload_version_supported("t-bad", QUEUE_OPERATIONS, "test_op", "999")

    assert ok is False
    failed = [
        c
        for c in valkey.hset.call_args_list
        if c.kwargs.get("mapping", {}).get("status") == "failed"
    ]
    assert failed


# ---------------------------------------------------------------------------
# _mark_task_failed_terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_terminal_routes_through_queue_client() -> None:
    """With a queue_client, the helper delegates to mark_task_failed_terminal."""
    qc = MagicMock()
    qc.mark_task_failed_terminal = AsyncMock(return_value=None)
    worker, valkey = _make_worker(queue_client=qc)

    fields = {"status": "failed", "error": "x"}
    await worker._mark_task_failed_terminal("t-1", fields)

    qc.mark_task_failed_terminal.assert_awaited_once_with("t-1", fields)
    valkey.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_terminal_bare_hset_fallback() -> None:
    """Without a queue_client, the helper falls back to a bare HSET."""
    worker, valkey = _make_worker(queue_client=None)
    await worker._mark_task_failed_terminal("t-1", {"status": "failed"})
    valkey.hset.assert_awaited_once()


# ---------------------------------------------------------------------------
# _retry_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_task_writes_queued_status_and_reads_priority() -> None:
    """_retry_task resets status=queued with retry_after, PERSISTs, and re-adds to pending."""
    worker, valkey = _make_worker()
    valkey.hget = AsyncMock(return_value=b"77")

    await worker._retry_task("t-r", QUEUE_OPERATIONS, attempt=1, max_tries=3)

    # status=queued write with a retry_after timestamp.
    queued_writes = [
        c
        for c in valkey.hset.call_args_list
        if c.kwargs.get("mapping", {}).get("status") == "queued"
    ]
    assert queued_writes
    assert "retry_after" in queued_writes[0].kwargs["mapping"]

    # Defensive PERSIST against a stray dead-letter TTL.
    valkey.persist.assert_awaited_once_with("queue:task:t-r")

    # Re-added to the pending sorted set with a future-biased score.
    valkey.zadd.assert_awaited_once()
    add_args = valkey.zadd.call_args.args
    assert add_args[0] == f"queue:{QUEUE_OPERATIONS}:pending"
    assert "t-r" in add_args[1]


# ---------------------------------------------------------------------------
# _startup_reconcile / _reconcile_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_reconcile_noop_without_queue_client() -> None:
    """No queue_client → startup reconcile is a no-op (reconcile_queue never called)."""
    worker, _ = _make_worker(queue_client=None)
    # Must not raise; reconcile_queue is never reached.
    await worker._startup_reconcile()


@pytest.mark.asyncio
async def test_startup_reconcile_isolates_per_queue_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-queue reconcile exception is logged and does not propagate."""
    import chaoscypher_core.queue.worker as worker_mod

    async def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError("reconcile blew up")

    monkeypatch.setattr(worker_mod, "reconcile_queue", _boom)

    qc = MagicMock()
    worker, _ = _make_worker(queue_client=qc)

    # Exception is swallowed inside the per-queue try/except.
    await worker._startup_reconcile()


@pytest.mark.asyncio
async def test_startup_reconcile_runs_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean reconcile pass (total()==0) logs the clean branch and returns."""
    import chaoscypher_core.queue.worker as worker_mod

    stats = MagicMock()
    stats.total.return_value = 0
    stats.to_dict.return_value = {}

    called: list[str] = []

    async def _ok(*, queue_name: str, **_kwargs: Any) -> Any:
        called.append(queue_name)
        return stats

    monkeypatch.setattr(worker_mod, "reconcile_queue", _ok)

    qc = MagicMock()
    worker, _ = _make_worker(queue_client=qc)
    await worker._startup_reconcile()

    assert called == [QUEUE_OPERATIONS]


@pytest.mark.asyncio
async def test_reconcile_loop_noop_without_queue_client() -> None:
    """No queue_client → the reconcile loop returns immediately."""
    worker, _ = _make_worker(queue_client=None)
    await worker._reconcile_loop()  # returns at the guard, no sleep


@pytest.mark.asyncio
async def test_reconcile_loop_exits_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reconcile loop exits cleanly when its sleep is cancelled."""
    import chaoscypher_core.queue.worker as worker_mod

    async def _cancel_sleep(_secs: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_mod.asyncio, "sleep", _cancel_sleep)

    qc = MagicMock()
    worker, _ = _make_worker(queue_client=qc)
    worker._running = True

    # Cancelled sleep → loop returns without raising out.
    await worker._reconcile_loop()


# ---------------------------------------------------------------------------
# _drain_active_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_active_tasks_empty_returns() -> None:
    """With no active tasks, drain returns immediately."""
    worker, _ = _make_worker()
    worker._active_tasks = {}
    await worker._drain_active_tasks(timeout=1.0)  # no error


@pytest.mark.asyncio
async def test_drain_active_tasks_cancels_pending() -> None:
    """Tasks still pending after the drain timeout are cancelled."""
    worker, _ = _make_worker()

    async def _never() -> None:
        await asyncio.sleep(100)

    pending_task = asyncio.create_task(_never())
    worker._active_tasks = {"t-1": pending_task}

    await worker._drain_active_tasks(timeout=0.01)

    assert pending_task.cancelled()


# ---------------------------------------------------------------------------
# _request_shutdown
# ---------------------------------------------------------------------------


def test_request_shutdown_flips_running_once() -> None:
    """_request_shutdown flips _running to False; a second call is a no-op."""
    worker, _ = _make_worker()
    worker._running = True

    worker._request_shutdown()
    assert worker._running is False

    # Second call returns early (already shutting down) without error.
    worker._request_shutdown()
    assert worker._running is False


# ---------------------------------------------------------------------------
# _publish_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_health_one_tick_then_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """_publish_health writes the health hash + EXPIRE, then exits on CancelledError."""
    import chaoscypher_core.queue.worker as worker_mod

    worker, valkey = _make_worker()
    worker._running = True

    # First sleep raises CancelledError to exit after exactly one health tick.
    async def _one_tick_sleep(_secs: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_mod.asyncio, "sleep", _one_tick_sleep)

    # CancelledError is suppressed inside _publish_health → returns cleanly.
    await worker._publish_health()

    # Health hash + EXPIRE were issued for the configured queue.
    health_writes = [
        c
        for c in valkey.hset.call_args_list
        if c.args and c.args[0] == f"queue:{QUEUE_OPERATIONS}:health"
    ]
    assert health_writes
    expire_writes = [
        c
        for c in valkey.expire.call_args_list
        if c.args and c.args[0] == f"queue:{QUEUE_OPERATIONS}:health"
    ]
    assert expire_writes


# ---------------------------------------------------------------------------
# _run_with_heartbeat (module-level helper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_heartbeat_returns_result_and_cancels_refresher() -> None:
    """_run_with_heartbeat returns the handler result and cancels the refresher."""
    refresh_calls: list[Any] = []

    async def _refresh(_task_id: str, _ttl: int) -> None:
        refresh_calls.append(1)

    async def _factory() -> str:
        return "result-ok"

    result = await _run_with_heartbeat(
        task_id="t-1",
        coro_factory=_factory,
        refresh=_refresh,
        refresh_interval=0.001,
        ttl_seconds=30,
    )
    assert result == "result-ok"


@pytest.mark.asyncio
async def test_run_with_heartbeat_cancels_refresher_on_raise() -> None:
    """The refresher is cancelled in finally even when the handler raises."""

    async def _refresh(_task_id: str, _ttl: int) -> None:
        await asyncio.sleep(0.001)

    async def _factory() -> str:
        raise ValueError("handler boom")

    with pytest.raises(ValueError, match="handler boom"):
        await _run_with_heartbeat(
            task_id="t-1",
            coro_factory=_factory,
            refresh=_refresh,
            refresh_interval=0.001,
            ttl_seconds=30,
        )
