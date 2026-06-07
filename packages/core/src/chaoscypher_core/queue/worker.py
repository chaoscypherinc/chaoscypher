# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Custom async queue worker with per-queue concurrency control.

Replaces ARQ's run_worker() with a lightweight implementation that uses
the existing ``queue:{name}:pending`` sorted sets as the primary job queue.
Each queue gets its own asyncio poller with a semaphore for concurrency.

Example:
    worker = QueueWorker(
        client=valkey_conn,
        queues_config={
            "llm": {"concurrency": 1, "max_tries": 5, "timeout": 3600},
            "operations": {"concurrency": 8, "max_tries": 5, "timeout": 3600},
        },
        handlers={"llm": {"chat": handler_fn}, "operations": {...}},
    )
    await worker.run()

"""

import asyncio
import contextlib
import json
import random
import signal
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.queue.reconciler import reconcile_queue
from chaoscypher_core.queue.service import _execute_handler, classify_error
from chaoscypher_core.queue.utils import iso_now as _iso_now


if TYPE_CHECKING:
    from valkey.asyncio import Valkey


logger = structlog.get_logger(__name__)


async def _await_result(result: Any) -> Any:
    """Await a queue client result if it's a coroutine, otherwise return it directly.

    Async clients may return either a coroutine or an immediate value
    depending on pipeline mode. This helper normalises both cases.

    Args:
        result: The return value from a queue command (may be awaitable).

    Returns:
        The resolved value.

    """
    if isinstance(result, (int, bool, str, bytes, list, dict, type(None))):
        return result
    return await result


async def _heartbeat_refresher(
    task_id: str,
    refresh: Callable[[str, int], Awaitable[None]],
    refresh_interval: float,
    ttl_seconds: int,
) -> None:
    """Refresh a task's heartbeat key at a fixed interval until cancelled.

    Runs on the SAME event loop as the handler coroutine. If the handler
    blocks the loop (CPU-bound work, deadlock), this task also stops
    firing, the heartbeat key expires, and the reconciler classifies
    the task as abandoned — exactly the desired behavior.

    Args:
        task_id: Task to refresh.
        refresh: Bound QueueClient.refresh_heartbeat callable.
        refresh_interval: Seconds between refreshes. Must be < ttl / 2.
        ttl_seconds: TTL applied on each refresh.
    """
    # CancelledError propagates naturally when the handler task completes
    # and this refresher coroutine is cancelled.
    while True:
        await asyncio.sleep(refresh_interval)
        await refresh(task_id, ttl_seconds)


async def _run_with_heartbeat(
    task_id: str,
    coro_factory: Callable[[], Awaitable[Any]],
    refresh: Callable[[str, int], Awaitable[None]],
    refresh_interval: float,
    ttl_seconds: int,
) -> Any:
    """Run a handler coroutine while a background task refreshes the heartbeat.

    The refresher is started as an asyncio.Task BEFORE the handler coroutine,
    and is cancelled in a finally block when the handler finishes or throws.
    Both tasks share the same event loop — this is a non-negotiable design
    constraint: if the refresher ran in a thread, it would keep pinging
    even when the handler is wedged, and the reconciler would never
    detect the abandoned task.

    Args:
        task_id: Task being executed.
        coro_factory: Zero-arg callable that returns the handler coroutine.
            A factory (not a pre-built coroutine) is used so the handler
            isn't created until the refresher is in place.
        refresh: Bound QueueClient.refresh_heartbeat.
        refresh_interval: Seconds between heartbeat refreshes.
        ttl_seconds: Heartbeat TTL.

    Returns:
        The handler's return value.

    Raises:
        Any exception raised by the handler is re-raised to the caller.
    """
    refresher = asyncio.create_task(
        _heartbeat_refresher(task_id, refresh, refresh_interval, ttl_seconds)
    )
    try:
        return await coro_factory()
    finally:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await refresher


class QueueWorker:
    """Async queue worker with per-queue concurrency control.

    Polls ``queue:{name}:pending`` sorted sets using ZPOPMAX, dispatches
    tasks to registered handlers, and manages lifecycle via signals.

    Attributes:
        client: Async Valkey connection.
        queues_config: Per-queue settings (concurrency, max_tries, timeout).
        handlers: Nested dict of queue -> operation -> handler callable.

    """

    def __init__(
        self,
        client: Valkey,
        queues_config: dict[str, dict[str, Any]],
        handlers: dict[str, dict[str, Any]],
        poll_interval: float = 0.5,
        health_report_interval: int = 2,
        drain_timeout: float = 30.0,
        semaphore_acquire_timeout: float = 1.0,
        poller_error_delay: float = 1.0,
        # Self-healing params
        queue_client: Any = None,
        heartbeat_ttl_seconds: int = 30,
        heartbeat_refresh_interval_seconds: int = 10,
        reconcile_interval_seconds: int = 30,
    ) -> None:
        """Initialize the worker.

        Args:
            client: Connected ``valkey.asyncio.Valkey`` instance.
            queues_config: Per-queue configuration, e.g.
                ``{"llm": {"concurrency": 1, "max_tries": 5, "timeout": 3600}}``.
            handlers: Registered task handlers keyed by queue then operation.
            poll_interval: Seconds between queue polls when idle (default 0.5).
            health_report_interval: Seconds between health report updates.
            drain_timeout: Maximum seconds to wait for in-flight tasks on shutdown.
            semaphore_acquire_timeout: Seconds to wait for a concurrency slot
                before re-checking the shutdown flag.
            poller_error_delay: Seconds to pause after a poller exception before
                re-entering the poll loop.
            queue_client: Typed QueueClient for self-healing features
                (heartbeat lifecycle, atomic-complete). When None, the worker
                degrades gracefully to bare SREM on completion.
            heartbeat_ttl_seconds: TTL for heartbeat keys in seconds.
            heartbeat_refresh_interval_seconds: Interval between heartbeat
                refreshes in seconds. Must be less than half of the TTL.
            reconcile_interval_seconds: Interval between periodic
                reconciliation passes in seconds.

        """
        self.client = client
        self.queues_config = queues_config
        self.handlers = handlers
        self.poll_interval = poll_interval
        self._health_report_interval = health_report_interval
        self._drain_timeout = drain_timeout
        self._semaphore_acquire_timeout = semaphore_acquire_timeout
        self._poller_error_delay = poller_error_delay

        self._running = False
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._pollers: list[asyncio.Task[Any]] = []
        self._background_tasks: list[asyncio.Task[Any]] = []

        # Self-healing: typed QueueClient for heartbeat lifecycle and
        # atomic-complete. In normal deployments this is always set.
        self._queue_client: Any = queue_client
        self._heartbeat_ttl_seconds: int = heartbeat_ttl_seconds
        self._heartbeat_refresh_interval_seconds: int = heartbeat_refresh_interval_seconds
        self._reconcile_interval_seconds: int = reconcile_interval_seconds

    def _allows_queue_transient_retry(self, queue_name: str, operation: str) -> bool:
        """Return whether the worker should retry transient failures.

        QueueClient carries the per-handler policy from HandlerSpec. When a
        worker is built without QueueClient (common in unit tests and simple
        harnesses), preserve the historical behavior and allow retries.
        """
        if self._queue_client is None:
            return True

        get_policy = getattr(self._queue_client, "get_transient_retry_policy", None)
        if get_policy is None:
            return True
        return bool(get_policy(queue_name, operation))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all pollers and background tasks, then wait for shutdown."""
        self._running = True
        self._install_signal_handlers()

        # Self-healing: clean up anything the previous worker instance
        # left behind (orphans, abandoned tasks) before starting to
        # claim new work. Safe no-op when _queue_client is not injected.
        await self._startup_reconcile()

        # Start a poller coroutine per queue
        for queue_name, config in self.queues_config.items():
            task = asyncio.create_task(
                self._poll_queue(queue_name, config),
                name=f"poller-{queue_name}",
            )
            self._pollers.append(task)

        # Start health publisher
        health_task = asyncio.create_task(
            self._publish_health(),
            name="health-publisher",
        )
        self._background_tasks.append(health_task)

        # Self-healing: periodic reconciliation loop. No-op when
        # _queue_client is not injected.
        reconcile_task = asyncio.create_task(
            self._reconcile_loop(),
            name="reconcile-loop",
        )
        self._background_tasks.append(reconcile_task)

        logger.info(
            "worker_started",
            queues=list(self.queues_config.keys()),
            total_handlers=sum(len(h) for h in self.handlers.values()),
        )

        # Wait until all pollers complete (they exit when self._running is False)
        await asyncio.gather(*self._pollers, return_exceptions=True)

        # Drain active tasks
        await self._drain_active_tasks(timeout=self._drain_timeout)

        # Cancel background tasks
        for bg in self._background_tasks:
            bg.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)

        # Clean up health keys
        for queue_name in self.queues_config:
            await self.client.delete(f"queue:{queue_name}:health")
            await self.client.delete(f"queue:{queue_name}:running")

        logger.info("worker_stopped")

    # ------------------------------------------------------------------
    # Queue Polling
    # ------------------------------------------------------------------

    async def _poll_queue(self, queue_name: str, config: dict[str, Any]) -> None:
        """Poll a single queue using ZPOPMAX with semaphore gating.

        Args:
            queue_name: Logical queue name (e.g. "llm").
            config: Queue configuration dict.

        """
        concurrency = config.get("concurrency", 1)
        semaphore = asyncio.Semaphore(concurrency)
        pending_key = f"queue:{queue_name}:pending"

        logger.info(
            "poller_started",
            queue=queue_name,
            concurrency=concurrency,
        )

        while self._running:
            # Wait for a semaphore slot (with timeout so we can check _running)
            try:
                acquired = False
                try:
                    await asyncio.wait_for(
                        semaphore.acquire(), timeout=self._semaphore_acquire_timeout
                    )
                    acquired = True
                except TimeoutError:
                    continue  # Re-check self._running

                # Pop highest-priority item (ZPOPMAX pops highest score first).
                # Scores encode priority minus a time fraction, so FIFO holds
                # within a priority tier.
                items = await self.client.zpopmax(pending_key, count=1)
                if not items:
                    semaphore.release()
                    acquired = False
                    await asyncio.sleep(self.poll_interval)
                    continue

                # Decode task ID
                raw_id, score = items[0]
                task_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id

                # Check if task is scheduled for future retry (backoff not yet elapsed).
                # retry_after is set by _retry_task() to time.time() + backoff.
                retry_after = await self._get_retry_after(task_id)
                if retry_after and retry_after > time.time():
                    # Not ready yet — re-add with same score and release slot
                    await _await_result(self.client.zadd(pending_key, {task_id: score}))
                    semaphore.release()
                    acquired = False
                    await asyncio.sleep(self.poll_interval)
                    continue

                # Spawn processing task (semaphore released on completion)
                process_task = asyncio.create_task(
                    self._process_task(task_id, queue_name, config, semaphore),
                    name=f"task-{task_id[:8]}",
                )
                self._active_tasks[task_id] = process_task

                # Define callback with explicit type hint for task parameter
                def task_done_callback(_t: asyncio.Task[Any], tid: str = task_id) -> None:
                    """Drop the finished task from the active-tasks registry."""
                    self._task_done(tid)

                process_task.add_done_callback(task_done_callback)
                # Do NOT release semaphore here — _process_task does it in finally

            except asyncio.CancelledError:
                if acquired:
                    semaphore.release()
                break
            except Exception:
                if acquired:
                    semaphore.release()
                logger.exception("poller_error", queue=queue_name)
                await asyncio.sleep(self._poller_error_delay)

        logger.info("poller_stopped", queue=queue_name)

    def _task_done(self, task_id: str) -> None:
        """Callback when a task future completes."""
        self._active_tasks.pop(task_id, None)

    async def _get_retry_after(self, task_id: str) -> float | None:
        """Read the retry_after timestamp from a task hash, if set.

        Args:
            task_id: Task to check.

        Returns:
            Epoch timestamp when the task becomes eligible for retry,
            or None if the task has no pending retry delay.

        """
        raw = await _await_result(self.client.hget(f"queue:task:{task_id}", "retry_after"))
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, AttributeError):  # fmt: skip
            return None

    # ------------------------------------------------------------------
    # Task Processing
    # ------------------------------------------------------------------

    async def _mark_task_failed_terminal(self, task_id: str, fields: dict[str, str]) -> None:
        """HSET terminal-failed fields and apply dead-letter retention TTL.

        Routes through ``QueueClient.mark_task_failed_terminal`` when the
        typed client is injected so the EXPIRE is paired with the HSET.
        Falls back to a bare HSET when ``_queue_client`` is ``None`` (the
        pre-injection path used by some test harnesses) so behavior matches
        the legacy code path for those callers.
        """
        if self._queue_client is not None:
            await self._queue_client.mark_task_failed_terminal(task_id, fields)
            return
        await _await_result(self.client.hset(f"queue:task:{task_id}", mapping=fields))

    async def _payload_version_supported(
        self,
        task_id: str,
        queue_name: str,
        operation: str,
        raw_version: str | None,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Return True if the task's payload_version is supported by this worker.

        Missing version is treated as v1 (transitional — this is the migration
        commit that introduces the field; pre-existing queued tasks still need
        to drain). Future-incompatible versions are marked failed permanently
        with no retry so they don't starve queue capacity. Before returning
        False the worker also calls into ``upgrade_recovery.apply_upgrade_recovery``
        so the owning resource (source row, chat) flips to a user-visible
        "interrupted by upgrade — retry" state instead of sitting in
        ``extracting`` / ``processing`` forever.
        """
        from chaoscypher_core.queue.client import SUPPORTED_PAYLOAD_VERSIONS

        payload_version = int(raw_version) if raw_version else 1
        if payload_version in SUPPORTED_PAYLOAD_VERSIONS:
            return True

        error_msg = (
            f"Unsupported payload_version={payload_version} "
            f"(supported={sorted(SUPPORTED_PAYLOAD_VERSIONS)})"
        )
        logger.error(
            "task_unsupported_payload_version",
            task_id=task_id,
            queue=queue_name,
            operation=operation,
            payload_version=payload_version,
            supported=sorted(SUPPORTED_PAYLOAD_VERSIONS),
        )

        # Mark-and-prompt recovery — transition the owning resource to a
        # retry-friendly state. Best-effort; the queue task is marked failed
        # regardless of recovery outcome.
        try:
            from chaoscypher_core.queue.upgrade_recovery import apply_upgrade_recovery

            apply_upgrade_recovery(
                operation=operation,
                data=data or {},
                metadata=metadata or {},
                task_id=task_id,
                payload_version=payload_version,
            )
        except Exception as exc:
            logger.warning(
                "upgrade_recovery_dispatch_failed",
                task_id=task_id,
                operation=operation,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        await self._mark_task_failed_terminal(
            task_id,
            {
                "status": "failed",
                "error": error_msg,
                "error_type": "permanent",
                "completed_at": _iso_now(),
            },
        )
        return False

    async def _process_task(  # noqa: PLR0911,PLR0912,PLR0915 — linear guard-return flow, each branch is a distinct dispatch outcome; dead-letter retention adds 2 terminal/non-terminal gates that read cleanest co-located with the existing fail sites
        self,
        task_id: str,
        queue_name: str,
        config: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> Any:
        """Process a single task: read hash, dispatch handler, update status.

        Args:
            task_id: The task's unique identifier.
            queue_name: Which queue this task came from.
            config: Queue configuration (max_tries, timeout, etc.).
            semaphore: Semaphore to release when done.

        Returns:
            Handler result, or None on failure.

        """
        running_key = f"queue:{queue_name}:running"
        # Track retry info to schedule AFTER semaphore release
        pending_retry: tuple[int, int] | None = None
        structlog.contextvars.bind_contextvars(task_id=task_id, queue=queue_name)
        try:
            # CLAIM ORDER: set the heartbeat key BEFORE adding to the
            # running set. This prevents the reconciler from observing
            # a just-claimed task as dead during the gap between the two
            # operations. When _queue_client is None (pre-injection
            # fallback), the heartbeat step is skipped and the bare
            # SREM cleanup path runs in finally.
            if self._queue_client is not None:
                await self._queue_client.set_heartbeat(
                    task_id, ttl_seconds=self._heartbeat_ttl_seconds
                )
            await _await_result(self.client.sadd(running_key, task_id))

            # Read task data from hash
            raw = await _await_result(self.client.hgetall(f"queue:task:{task_id}"))
            if not raw:
                logger.warning("task_hash_missing", task_id=task_id, queue=queue_name)
                return None

            # Decode bytes
            task_data = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in raw.items()
            }

            operation = task_data.get("operation", "")
            data = json.loads(task_data.get("data", "{}"))
            metadata = json.loads(task_data.get("metadata", "{}"))
            result_ttl = int(task_data.get("result_ttl", "3600"))
            attempts = int(task_data.get("attempts", "0"))
            max_tries = config["max_tries"]
            timeout = config["timeout"]

            # Payload-version gate: refuse to dispatch unknown versions.
            # Pass data + metadata so the recovery handler can find the
            # owning resource (source_id / chat_id) when marking it as
            # interrupted-by-upgrade.
            if not await self._payload_version_supported(
                task_id,
                queue_name,
                operation,
                task_data.get("payload_version"),
                data=data,
                metadata=metadata,
            ):
                return None

            # Increment attempts
            await _await_result(self.client.hincrby(f"queue:task:{task_id}", "attempts", 1))

            # Look up handler
            handler = self.handlers.get(queue_name, {}).get(operation)
            if handler is None:
                error_msg = f"No handler registered for {queue_name}:{operation}"
                logger.error("no_handler", task_id=task_id, queue=queue_name, operation=operation)
                await self._mark_task_failed_terminal(
                    task_id,
                    {
                        "status": "failed",
                        "error": error_msg,
                        "completed_at": _iso_now(),
                    },
                )
                return None

            # Mark running
            await _await_result(
                self.client.hset(
                    f"queue:task:{task_id}",
                    mapping={"status": "running", "started_at": _iso_now()},
                )
            )

            # Execute with timeout. When _queue_client is available,
            # _run_with_heartbeat drives a background asyncio.Task on the
            # same event loop that refreshes the heartbeat TTL, so a
            # handler that hangs also stops the heartbeat.
            # Dead-letter retention TTL for the failed task hash. Only the
            # *last* attempt of a transient error becomes a true terminal
            # failure (worker schedules no retry); the handler's permanent
            # path applies the TTL unconditionally because permanent
            # failures never retry. ``_execute_handler`` handles that gating.
            failed_result_ttl = (
                self._queue_client.failed_result_ttl if self._queue_client is not None else None
            )

            def _make_handler_coro() -> Awaitable[Any]:
                """Build the timeout-wrapped handler coroutine on demand.

                Deferred construction lets ``_run_with_heartbeat`` start the
                refresher task before the handler coroutine exists, so a
                never-awaited coroutine is never created if refresher setup
                fails. Invoked exactly once per task attempt.
                """
                return asyncio.wait_for(
                    _execute_handler(
                        handler,
                        task_id,
                        queue_name,
                        operation,
                        data,
                        metadata,
                        result_ttl,
                        self.client,
                        failed_result_ttl=failed_result_ttl,
                    ),
                    timeout=timeout,
                )

            try:
                if self._queue_client is not None:
                    return await _run_with_heartbeat(
                        task_id=task_id,
                        coro_factory=_make_handler_coro,
                        refresh=self._queue_client.refresh_heartbeat,
                        refresh_interval=self._heartbeat_refresh_interval_seconds,
                        ttl_seconds=self._heartbeat_ttl_seconds,
                    )
                return await _make_handler_coro()

            except TimeoutError:
                error_msg = f"Task timed out after {timeout}s"
                logger.exception("task_timeout", task_id=task_id, timeout=timeout)
                failed_fields = {
                    "status": "failed",
                    "error": error_msg,
                    "error_type": "transient",
                    "completed_at": _iso_now(),
                }
                # Schedule retry AFTER semaphore release (see finally block).
                # Only apply the dead-letter retention TTL when this is the
                # last attempt — otherwise ``_retry_task`` resets the status
                # back to ``queued`` and a long EXPIRE would auto-delete a
                # healthy in-flight task.
                will_retry = (
                    self._allows_queue_transient_retry(queue_name, operation)
                    and attempts + 1 < max_tries
                )
                if will_retry:
                    pending_retry = (attempts + 1, max_tries)
                    await _await_result(
                        self.client.hset(f"queue:task:{task_id}", mapping=failed_fields)
                    )
                else:
                    await self._mark_task_failed_terminal(task_id, failed_fields)
                return None

            except asyncio.CancelledError:
                logger.info("task_cancelled_by_worker", task_id=task_id)
                await _await_result(
                    self.client.hset(
                        f"queue:task:{task_id}",
                        mapping={
                            "status": "cancelled",
                            "error": "Task cancelled during shutdown",
                            "completed_at": _iso_now(),
                        },
                    )
                )
                raise

            except Exception as exc:
                # _execute_handler already updates the hash for permanent errors
                # (and applies the dead-letter retention TTL itself).  For
                # transient errors it re-raises, so we handle retry here.
                error_type = classify_error(exc)
                transient_retry_allowed = self._allows_queue_transient_retry(queue_name, operation)
                if (
                    error_type == "transient"
                    and transient_retry_allowed
                    and attempts + 1 < max_tries
                ):
                    pending_retry = (attempts + 1, max_tries)
                elif error_type == "transient" and self._queue_client is not None:
                    # Terminal transient — no retry budget left. Apply the
                    # dead-letter retention TTL on top of the failed-status
                    # write that ``_execute_handler`` already performed.
                    await _await_result(
                        self.client.expire(
                            f"queue:task:{task_id}",
                            self._queue_client.failed_result_ttl,
                        )
                    )
                # For permanent errors, _execute_handler already marked as failed.
                return None

        finally:
            # COMPLETE ORDER: atomic SREM + DEL heartbeat via Lua. Both
            # operations happen or neither does, preventing a reconciler
            # race where the task looks abandoned between the two cleanup
            # calls. Falls back to bare SREM when _queue_client is not
            # injected.
            if self._queue_client is not None:
                await self._queue_client.complete_task_atomic(queue_name, task_id)
            else:
                await _await_result(self.client.srem(running_key, task_id))

            # Release semaphore BEFORE any retry scheduling to avoid blocking
            # the queue slot during backoff (especially critical for LLM queue
            # with concurrency=1).
            semaphore.release()

            # Schedule retry after semaphore is released
            if pending_retry is not None:
                await self._retry_task(task_id, queue_name, pending_retry[0], pending_retry[1])

            structlog.contextvars.unbind_contextvars("task_id", "queue")

    async def _retry_task(
        self, task_id: str, queue_name: str, attempt: int, max_tries: int
    ) -> None:
        """Re-add a task to the pending queue with a delayed retry_after timestamp.

        Stores a ``retry_after`` epoch timestamp in the task hash so the poller
        can check whether the backoff has elapsed before processing. The task is
        also re-added with a future-biased score so it sorts after ready items.

        Backoff schedule: 5s, 15s, 35s, 75s, 155s base (exponential) with
        random jitter at 50-150% of the base to avoid thundering herd.

        Args:
            task_id: Task to retry.
            queue_name: Queue to re-add to.
            attempt: Current attempt number (1-based).
            max_tries: Maximum allowed attempts.

        """
        base_backoff = min(5 * (2 ** (attempt - 1)), 300)
        backoff = base_backoff * (0.5 + random.random())  # noqa: S311 — non-crypto jitter

        logger.info(
            "task_retry_scheduled",
            task_id=task_id,
            queue=queue_name,
            attempt=attempt,
            max_tries=max_tries,
            backoff_seconds=round(backoff, 1),
        )

        # Reset status to queued
        await _await_result(
            self.client.hset(
                f"queue:task:{task_id}",
                mapping={
                    "status": "queued",
                    "error": "",
                    "error_type": "",
                    "retry_after": str(time.time() + backoff),
                },
            )
        )

        # Defensive: clear any dead-letter retention TTL that an earlier
        # terminal-fail write may have applied. The worker's gating SHOULD
        # ensure the TTL is only set when no retry is scheduled, but PERSIST
        # is a cheap belt-and-suspenders against a re-queue silently
        # auto-deleting a healthy task hash.
        await _await_result(self.client.persist(f"queue:task:{task_id}"))

        # Get original priority
        raw_priority = await _await_result(self.client.hget(f"queue:task:{task_id}", "priority"))
        priority = float(
            raw_priority.decode() if isinstance(raw_priority, bytes) else raw_priority or "50"
        )

        # Composite score: priority minus a future-time fraction so the
        # task sorts BELOW currently-ready items (ZPOPMAX pops highest
        # score first). Effect: the task is gated until the reconciler or
        # `_get_retry_after` check clears the retry window.
        future_time = time.time() + backoff
        score = priority - future_time / 1e10
        await _await_result(self.client.zadd(f"queue:{queue_name}:pending", {task_id: score}))

    # ------------------------------------------------------------------
    # Health Publishing
    # ------------------------------------------------------------------

    async def _publish_health(self) -> None:
        """Publish worker health to queue server every 2 seconds.

        Writes a hash per queue with running count and timestamp, with a 10s TTL
        so the key auto-expires if the worker dies.
        """
        try:
            while self._running:
                for queue_name in self.queues_config:
                    health_key = f"queue:{queue_name}:health"
                    running_count = await _await_result(
                        self.client.scard(f"queue:{queue_name}:running")
                    )
                    queued_count = await _await_result(
                        self.client.zcard(f"queue:{queue_name}:pending")
                    )
                    await _await_result(
                        self.client.hset(
                            health_key,
                            mapping={
                                "running": str(running_count),
                                "queued": str(queued_count),
                                "timestamp": _iso_now(),
                                "concurrency": str(
                                    self.queues_config[queue_name].get("concurrency", 1)
                                ),
                            },
                        )
                    )
                    await _await_result(self.client.expire(health_key, 10))

                await asyncio.sleep(self._health_report_interval)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Graceful Shutdown
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM/SIGINT handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda *_a: self._request_shutdown())

    def _request_shutdown(self) -> None:
        """Signal handler: request graceful shutdown."""
        if not self._running:
            return  # Already shutting down
        logger.info("shutdown_requested")
        self._running = False

    async def _drain_active_tasks(self, timeout: float) -> None:
        """Wait for active tasks to finish, then cancel stragglers.

        Args:
            timeout: Maximum seconds to wait for in-flight tasks.

        """
        if not self._active_tasks:
            return

        logger.info("draining_active_tasks", count=len(self._active_tasks))

        tasks = list(self._active_tasks.values())
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        if pending:
            logger.warning("cancelling_remaining_tasks", count=len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        logger.info(
            "drain_complete",
            completed=len(done),
            cancelled=len(pending),
        )

    # ------------------------------------------------------------------
    # Self-healing reconciliation
    # ------------------------------------------------------------------

    async def _startup_reconcile(self) -> None:
        """Run one reconciliation pass across all configured queues.

        Called once by ``run()`` before the poll loop starts so that
        orphan IDs or abandoned tasks left behind by a previous worker
        instance get cleaned up immediately. Each queue is reconciled
        independently; per-queue errors are logged and do not stop
        reconciliation of the remaining queues.

        When ``_queue_client`` is None (pre-factory injection), the
        method is a no-op — the reconciler needs the typed client
        for heartbeat/atomic-complete access.
        """
        if self._queue_client is None:
            logger.info("startup_reconcile_skipped", reason="no_queue_client")
            return

        for queue_name, config in self.queues_config.items():
            try:
                stats = await reconcile_queue(
                    client=self._queue_client,
                    queue_name=queue_name,
                    max_tries=config["max_tries"],
                    timeout_seconds=config.get("timeout"),
                )
                if stats.total() > 0:
                    logger.warning(
                        "startup_reconcile_complete",
                        queue=queue_name,
                        **stats.to_dict(),
                    )
                else:
                    logger.info("startup_reconcile_clean", queue=queue_name)
            except Exception as exc:
                logger.exception(
                    "startup_reconcile_error",
                    queue=queue_name,
                    error=str(exc),
                )

    async def _reconcile_loop(self) -> None:
        """Periodically run reconcile_queue for all configured queues.

        Runs until ``_running`` is set to False. Intended to be started
        as an asyncio task alongside the main pollers in ``run()``.
        Per-queue errors are logged and the loop continues — a transient
        Valkey blip should not permanently disable reconciliation.
        """
        if self._queue_client is None:
            logger.info("reconcile_loop_skipped", reason="no_queue_client")
            return

        while self._running:
            try:
                await asyncio.sleep(self._reconcile_interval_seconds)
            except asyncio.CancelledError:
                return

            for queue_name, config in self.queues_config.items():
                # mypy narrows self._running to True inside the while-loop, but
                # _stop() can flip it during the await above. Re-check defensively.
                if not self._running:  # type: ignore[unreachable]
                    return  # type: ignore[unreachable]
                try:
                    stats = await reconcile_queue(
                        client=self._queue_client,
                        queue_name=queue_name,
                        max_tries=config["max_tries"],
                        timeout_seconds=config.get("timeout"),
                    )
                    if stats.total() > 0:
                        logger.warning(
                            "periodic_reconcile_found_tasks",
                            queue=queue_name,
                            **stats.to_dict(),
                        )
                except Exception as exc:
                    logger.exception(
                        "periodic_reconcile_error",
                        queue=queue_name,
                        error=str(exc),
                    )
