# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction task recovery on worker startup.

Handles recovery of orphaned chunk extraction tasks that were left in
``queued`` or ``running`` status when the worker died, plus re-queuing
logic for individual tasks.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

from chaoscypher_core.queue import decode_bytes, queue_client
from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.app_config import Settings


logger = get_logger(__name__)

__all__ = [
    "recover_orphaned_extraction_tasks",
    "requeue_extraction_task",
]


async def recover_orphaned_extraction_tasks(
    adapter: SqliteAdapter,
    database_name: str,
    settings: Settings,
) -> dict[str, int]:
    """Recover extraction tasks orphaned when the worker died.

    Finds chunk tasks with status='queued' or 'running' that have no
    corresponding task in queue.

    Args:
        adapter: SQLite adapter for database operations.
        database_name: Current database context.
        settings: Application settings.

    Returns:
        Dictionary with counts: {"recovered": N, "skipped": M, "failed": K}

    """
    orphaned_tasks = adapter.list_orphaned_chunk_tasks(database_name)

    if not orphaned_tasks:
        logger.debug("recovery_no_orphaned_tasks")
        return {"recovered": 0, "skipped": 0, "failed": 0}

    logger.info("recovery_scan_found_orphaned_tasks", count=len(orphaned_tasks))

    recovered = skipped = failed = 0

    for task in orphaned_tasks:
        queue_task_id = task.get("queue_task_id")
        task_id = task.get("id")

        # Check if task still exists in queue (queued or running)
        if queue_client.client and queue_task_id:
            task_exists = await queue_client.client.exists(f"queue:task:{queue_task_id}")
            if task_exists:
                # Check the actual status
                status_raw = await cast(
                    "Any", queue_client.client.hget(f"queue:task:{queue_task_id}", "status")
                )
                status = decode_bytes(status_raw) if status_raw else None
                if status in ("queued", "running"):
                    logger.debug(
                        "recovery_skip_active_task",
                        task_id=task_id,
                        queue_task_id=queue_task_id,
                        status=status,
                    )
                    skipped += 1
                    continue

        # Verify parent job is still active
        job_id = task.get("job_id")
        if not job_id or not task_id:
            logger.warning(
                "recovery_task_missing_id",
                task_id=task_id,
                job_id=job_id,
            )
            skipped += 1
            continue
        job = adapter.get_extraction_job(job_id)
        if not job or job.get("status") not in ("running", "pending"):
            logger.debug(
                "recovery_skip_inactive_job",
                task_id=task_id,
                job_id=job_id,
                job_status=job.get("status") if job else None,
            )
            skipped += 1
            continue

        # Check retry count — permanently fail tasks that exceeded retries
        retry_count = task.get("retry_count", 0)
        max_retries = task["max_retries"]
        if retry_count >= max_retries:
            logger.warning(
                "extraction_task_dead_lettered",
                task_id=task_id,
                job_id=job_id,
                source_id=job.get("source_id"),
                chunk_index=task.get("chunk_index"),
                retry_count=retry_count,
                max_retries=max_retries,
                last_error=task.get("error_message"),
                last_error_type=task.get("error_type"),
                database_name=task.get("database_name"),
            )
            adapter.fail_chunk_task(task_id, "Max retries exceeded during recovery", "max_retries")
            adapter.increment_job_completed_and_check(
                job_id=job_id,
                database_name=task.get("database_name") or "default",
                outcome="failed",
            )
            failed += 1
            continue

        try:
            await requeue_extraction_task(adapter, task, job, settings)
            recovered += 1
            logger.info(
                "recovery_task_requeued",
                task_id=task_id,
                chunk_index=task.get("chunk_index"),
                job_id=job_id,
            )
        except Exception as e:
            failed += 1
            logger.exception(
                "recovery_task_failed",
                task_id=task_id,
                job_id=job_id,
                source_id=job.get("source_id"),
                chunk_index=task.get("chunk_index"),
                retry_count=task.get("retry_count", 0),
                error=str(e),
                error_type=type(e).__name__,
            )
            with contextlib.suppress(Exception):
                adapter.fail_chunk_task(task_id, f"Recovery failed: {e}", "recovery_error")
            # Mirror the max-retries branch above: a chunk task that can no
            # longer be requeued is terminal, so its job-completion counter
            # must advance or the parent job never reaches its terminal check
            # and stays "running" forever with all chunk tasks exhausted.
            # Independent suppress so a fail_chunk_task error cannot skip it.
            with contextlib.suppress(Exception):
                adapter.increment_job_completed_and_check(
                    job_id=job_id,
                    database_name=task.get("database_name") or "default",
                    outcome="failed",
                )

    return {"recovered": recovered, "skipped": skipped, "failed": failed}


async def requeue_extraction_task(
    adapter: SqliteAdapter,
    task: dict[str, Any],
    job: dict[str, Any],
    settings: Settings,
) -> str:
    """Re-queue an orphaned extraction task.

    Args:
        adapter: SQLite adapter for database operations.
        task: Task dictionary from list_running_chunk_tasks.
        job: Parent extraction job dictionary.
        settings: Application settings.

    Returns:
        New queue task ID.

    Raises:
        ValueError: If hierarchical group cannot be found.

    """
    from chaoscypher_core.operations.extraction import (
        ChunkExtractionOperationsService,
    )

    task_id = task["id"]
    chunk_index = task["chunk_index"]
    source_id = job["source_id"]

    groups = adapter.get_hierarchical_groups(
        source_id=source_id,
        database_name=task["database_name"],
    )

    group = next((g for g in groups if g.get("group_index") == chunk_index), None)
    if not group:
        msg = f"Could not find hierarchical group for chunk {chunk_index}"
        raise ValueError(msg)

    adapter.update_chunk_task(
        task_id,
        {
            "status": "pending",
            "queue_task_id": None,
            "started_at": None,
            "error_message": None,
            "error_type": None,
            "retry_count": task.get("retry_count", 0) + 1,
            "hierarchical_group_id": group["id"],
        },
    )

    # Only the queue_extract_chunk method is needed here (it delegates to
    # queue_client.enqueue_task without using repository dependencies).
    extraction_service = ChunkExtractionOperationsService(
        source_repository=adapter,
    )
    new_queue_task_id = await extraction_service.queue_extract_chunk(
        chunk_task_id=task_id,
        job_id=task["job_id"],
        database_name=task["database_name"],
        chunk_index=chunk_index,
        hierarchical_group_id=group["id"],
        small_chunk_ids=group.get("small_chunk_ids", []),
        priority=settings.priorities.background,
    )

    adapter.mark_chunk_task_queued(task_id, new_queue_task_id)
    return str(new_queue_task_id)
