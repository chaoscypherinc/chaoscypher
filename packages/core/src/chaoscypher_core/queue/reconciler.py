# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue self-healing reconciler.

Scans queue:{queue}:running for orphan or abandoned task IDs and
recovers them per handler policy. See the design spec at
queue reconciliation design notes for the
classification matrix and architectural rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.queue.utils import iso_now as _iso_now


if TYPE_CHECKING:
    from chaoscypher_core.queue.client import QueueClient

logger = structlog.get_logger(__name__)


@dataclass
class ReconcileStats:
    """Counters produced by a single reconciliation pass.

    Attributes:
        recovered_orphans: Tasks with an ID in the running set but no
            backing hash — removed as orphans.
        recovered_crashed: Tasks abandoned by a crashed worker that
            were requeued (retry_on_crash=True, attempts < max_tries).
        failed_unrecoverable: Tasks abandoned by a crashed worker that
            were marked failed (retry_on_crash=False or attempts
            exhausted).
    """

    recovered_orphans: int = 0
    recovered_crashed: int = 0
    failed_unrecoverable: int = 0

    def total(self) -> int:
        """Total number of tasks acted on in this pass."""
        return self.recovered_orphans + self.recovered_crashed + self.failed_unrecoverable

    def merge(self, other: ReconcileStats) -> None:
        """Accumulate counts from another ReconcileStats into this one."""
        self.recovered_orphans += other.recovered_orphans
        self.recovered_crashed += other.recovered_crashed
        self.failed_unrecoverable += other.failed_unrecoverable

    def to_dict(self) -> dict[str, int]:
        """Serialize for JSON API responses and Valkey persistence."""
        return {
            "recovered_orphans": self.recovered_orphans,
            "recovered_crashed": self.recovered_crashed,
            "failed_unrecoverable": self.failed_unrecoverable,
        }


def _recovery_counters_key(queue_name: str) -> str:
    """Valkey hash key where persistent recovery counters live.

    Kept separate from ``queue:{queue}:stats`` (which is already used
    by the queue monitor for LLM cost tracking) to avoid field name
    collisions.
    """
    return f"queue:{queue_name}:recovery_counters"


async def _persist_counters(client: QueueClient, queue_name: str, stats: ReconcileStats) -> None:
    """Accumulate reconciliation counters into the persistent hash.

    Called by every reconcile_queue invocation so counters accumulate
    across worker restarts AND across the different reconciler call
    sites (worker startup, worker periodic loop, Cortex safety net,
    admin API). No-op when nothing was recovered.
    """
    if stats.total() == 0 or client.client is None:
        return
    stats_key = _recovery_counters_key(queue_name)
    for field, value in stats.to_dict().items():
        if value > 0:
            await client.client.hincrby(stats_key, field, value)


async def reconcile_queue(
    client: QueueClient,
    queue_name: str,
    *,
    max_tries: int = 5,
    timeout_seconds: int | None = None,
) -> ReconcileStats:
    """Run one reconciliation pass over a queue's running set.

    For each task ID in queue:{queue}:running, classify as:

    - orphan: hash missing AND heartbeat missing -> SREM, count as orphan
    - timed_out: hash present, started_at + timeout_seconds < now -> abandon
      (regardless of heartbeat liveness — catches event-loop hangs that keep
      the heartbeat firing while the actual work stalls)
    - healthy: hash present AND heartbeat present AND not timed out -> skip
    - abandoned: hash present but heartbeat missing -> recover per policy

    Args:
        client: QueueClient instance (must have a live Valkey connection).
        queue_name: Queue name ("llm" or "operations").
        max_tries: Max retry attempts before giving up on a task.
        timeout_seconds: Absolute upper bound on task run time in seconds.
            When set, a task whose ``started_at`` is older than this
            threshold is classified as abandoned even if its heartbeat key
            is still present. Pass ``None`` (default) to disable the check
            and rely solely on heartbeat liveness.

    Returns:
        ReconcileStats with per-category counters.
    """
    stats = ReconcileStats()
    if client.client is None:
        logger.warning("reconcile_skipped_no_client", queue=queue_name)
        return stats

    # Pre-compute the cutoff once for the whole pass so all tasks in the
    # same reconciliation cycle share a consistent reference point.
    cutoff: datetime | None = None
    if timeout_seconds is not None:
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)

    running_key = f"queue:{queue_name}:running"
    task_ids = await client.client.smembers(running_key)

    for raw_id in task_ids:
        task_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id

        hash_exists = bool(await client.client.exists(f"queue:task:{task_id}"))
        heartbeat_exists = bool(await client.client.exists(f"queue:task:{task_id}:heartbeat"))

        if not hash_exists and not heartbeat_exists:
            # Orphan: ID with no backing data
            await client.client.srem(running_key, task_id)
            stats.recovered_orphans += 1
            logger.warning(
                "task_recovered",
                task_id=task_id,
                queue=queue_name,
                reason="orphan",
                action="removed",
            )
            continue

        # Absolute timeout check: fetch the hash to read started_at.
        # This runs regardless of heartbeat status so that event-loop hangs
        # that keep the heartbeat alive are still caught.
        if hash_exists and cutoff is not None:
            raw_hash = await client.client.hgetall(f"queue:task:{task_id}")
            task_hash: dict[str, str] = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in raw_hash.items()
            }
            started_at_str = task_hash.get("started_at", "")
            if started_at_str:
                try:
                    started_at = datetime.fromisoformat(started_at_str)
                except ValueError:
                    started_at = None
                if started_at is not None and started_at < cutoff:
                    logger.warning(
                        "task_abandoned_timeout",
                        task_id=task_id,
                        queue=queue_name,
                        started_at=started_at_str,
                        timeout_seconds=timeout_seconds,
                    )
                    await _handle_abandoned(
                        client=client,
                        queue_name=queue_name,
                        task_id=task_id,
                        max_tries=max_tries,
                        stats=stats,
                    )
                    continue

        if hash_exists and heartbeat_exists:
            # Healthy: hash present, heartbeat alive, not timed out
            continue

        # Remaining case — hash_exists AND NOT heartbeat_exists — is
        # the "abandoned" classification: worker crashed or handler hung
        # long enough for the heartbeat key's TTL to expire.
        if hash_exists and not heartbeat_exists:
            await _handle_abandoned(
                client=client,
                queue_name=queue_name,
                task_id=task_id,
                max_tries=max_tries,
                stats=stats,
            )

    # Persist counters so every reconciler call site (startup, periodic,
    # Cortex safety net, admin API) contributes to the long-term trend.
    await _persist_counters(client, queue_name, stats)

    return stats


async def _handle_abandoned(
    *,
    client: QueueClient,
    queue_name: str,
    task_id: str,
    max_tries: int,
    stats: ReconcileStats,
) -> None:
    """Recover or fail a task abandoned by a crashed worker.

    Consults the handler's retry_on_crash flag via
    QueueClient.get_retry_policy. If the handler opted in AND the task
    hasn't exceeded max_tries, the task is requeued (reset status,
    re-added to pending sorted set). Otherwise it's marked failed with
    error_type=worker_crashed.

    Args:
        client: QueueClient with a live Valkey connection.
        queue_name: Queue name.
        task_id: Abandoned task ID.
        max_tries: Retry budget from queue config.
        stats: Mutable stats accumulator.
    """
    if client.client is None:
        return

    running_key = f"queue:{queue_name}:running"
    task_key = f"queue:task:{task_id}"

    raw = await client.client.hgetall(task_key)
    task = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }
    operation = task.get("operation", "")
    attempts = int(task.get("attempts", "0"))
    priority = float(task.get("priority", "0"))

    retry_allowed = client.get_retry_policy(queue_name, operation)

    if retry_allowed and attempts < max_tries:
        # Requeue: reset status, re-add to pending, THEN remove from running.
        # IMPORTANT: zadd must run before srem so the task is never absent
        # from BOTH the running set and the pending queue at the same time.
        # The reconciler only scans queue:{queue}:running, so a task srem'd
        # out of running before a failed zadd would sit in neither set and be
        # invisible to every future reconcile cycle — an undetectable limbo.
        # Adding to pending first and removing from running only on zadd
        # success means a transient Valkey error during zadd leaves the task
        # in the running set for the next cycle to retry. attempts increments
        # only after a successful requeue so that same transient error never
        # silently consumes the retry budget.
        await client.client.hset(
            task_key,
            mapping={
                "status": "queued",
                "error": "",
                "error_type": "",
            },
        )
        # Defensive: clear any dead-letter retention TTL a prior terminal-
        # fail write may have left behind. Pairs with the matching PERSIST
        # in ``QueueWorker._retry_task`` so a re-queue never auto-deletes a
        # healthy task hash.
        await client.client.persist(task_key)
        try:
            await client.client.zadd(f"queue:{queue_name}:pending", {task_id: priority})
        except Exception:
            logger.exception(
                "reconciler_requeue_failed",
                task_id=task_id,
                queue=queue_name,
                attempts=attempts,
            )
            return  # task left in the running set; next reconcile retries

        await client.client.srem(running_key, task_id)
        await client.client.hincrby(task_key, "attempts", 1)
        stats.recovered_crashed += 1
        logger.warning(
            "task_recovered",
            task_id=task_id,
            queue=queue_name,
            reason="worker_crashed",
            action="requeued",
            attempts=attempts,
        )
    else:
        # Fail permanently — terminal status, apply dead-letter retention
        # TTL so the post-mortem hash stays around for operator review
        # (default 14 days; configurable via TimeoutSettings.failed_result_ttl).
        await client.client.srem(running_key, task_id)
        await client.mark_task_failed_terminal(
            task_id,
            {
                "status": "failed",
                "error": (
                    "Worker crashed or handler hung — recovery policy denies retry"
                    if not retry_allowed
                    else f"Worker crashed after {attempts} attempts (max {max_tries})"
                ),
                "error_type": "worker_crashed",
                "completed_at": _iso_now(),
            },
        )
        stats.failed_unrecoverable += 1
        logger.warning(
            "task_recovered",
            task_id=task_id,
            queue=queue_name,
            reason="worker_crashed",
            action="failed",
            attempts=attempts,
            retry_allowed=retry_allowed,
        )
