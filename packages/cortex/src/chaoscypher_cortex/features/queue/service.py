# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue Service.

Business logic for queue operations (thin wrapper around queue_client).
"""

from typing import Any, cast

import structlog

from chaoscypher_core.exceptions import (
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)

# Import existing queue client (infrastructure)
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.reconciler import (
    ReconcileStats,
    reconcile_queue,
)
from chaoscypher_cortex.features.queue.models import (
    CancelAllResponse,
    CancelBatchResponse,
    CancelByMetadataResponse,
    CancelTaskResponse,
    ClearHistoryResponse,
    QueueHealthResponse,
    QueueStatsResponse,
    QueueTaskResponse,
    RetryTaskResponse,
    TaskListResponse,
    TaskResultResponse,
)
from chaoscypher_cortex.shared.api.models import PaginationMetadata


logger = structlog.get_logger(__name__)


class QueueService:
    """Service for queue operations (wraps existing queue_client)."""

    def __init__(self) -> None:
        """Initialize queue service."""
        self.queue_client = queue_client

    def _check_available(self) -> None:
        """Check if queue is available."""
        if not self.queue_client.is_available:
            msg = "Queue"
            raise ExternalServiceError(msg, "Service unavailable")

    async def enqueue_task(
        self,
        queue: str,
        operation: str,
        data: dict[str, Any],
        priority: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QueueTaskResponse:
        """Queue a new task."""
        self._check_available()

        from chaoscypher_core.app_config import get_settings

        effective_priority = (
            priority if priority is not None else get_settings().priorities.background
        )
        task_id = await self.queue_client.enqueue_task(
            queue=queue,
            operation=operation,
            data=data,
            priority=effective_priority,
            metadata=metadata or {},
        )
        return QueueTaskResponse(task_id=task_id)

    async def list_tasks(
        self,
        page: int = 1,
        page_size: int | None = None,
        queues: list[str] | None = None,
    ) -> TaskListResponse:
        """List recent tasks (canonical {data, pagination} envelope)."""
        from chaoscypher_core.app_config import get_settings

        settings = get_settings()
        effective_page_size = min(
            page_size if page_size is not None else settings.pagination.default_page_size,
            settings.pagination.max_page_size,
        )

        if not self.queue_client.is_available:
            return TaskListResponse(
                data=[],
                pagination=PaginationMetadata(
                    total=0,
                    page=page,
                    page_size=effective_page_size,
                    total_pages=1,
                    has_next=False,
                    has_prev=False,
                ),
                total_in_queue=0,
                queues=queues,
            )

        offset = (page - 1) * effective_page_size

        tasks = await self.queue_client.get_recent_tasks(
            limit=effective_page_size, offset=offset, queues=queues
        )
        total = await self.queue_client.get_recent_tasks_count(queues=queues)

        # Active-tasks counter (queued + running across matched queues),
        # not a pagination metric — surfaced as a sibling for the UI's
        # "N tasks in queue" indicator.
        total_in_queue = 0
        try:
            all_stats = await self.queue_client.get_all_stats()
            for stat in all_stats:
                if queues is None or stat.get("queue") in queues:
                    total_in_queue += stat.get("queued", 0) + stat.get("running", 0)
        except Exception:
            logger.warning("failed_to_get_queue_stats_for_total")
            total_in_queue = len(tasks)

        total_pages = (
            (total + effective_page_size - 1) // effective_page_size
            if total > 0 and effective_page_size > 0
            else 1
        )

        return TaskListResponse(
            data=tasks,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                page_size=effective_page_size,
                total_pages=total_pages,
                has_next=page < total_pages,
                has_prev=page > 1,
            ),
            total_in_queue=total_in_queue,
            queues=queues,
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """Get task details."""
        self._check_available()

        task = await self.queue_client.get_task(task_id)
        if not task:
            msg = "Task"
            raise NotFoundError(msg, task_id)
        return task

    async def get_task_result(self, task_id: str) -> TaskResultResponse:
        """Get task result."""
        self._check_available()

        result = await self.queue_client.get_result(task_id)
        if result is None:
            msg = "TaskResult"
            raise NotFoundError(msg, task_id)
        return TaskResultResponse(result=result)

    async def cancel_task(self, task_id: str) -> CancelTaskResponse:
        """Cancel a task."""
        self._check_available()

        # Check if task exists first
        task = await self.queue_client.get_task(task_id)
        if not task:
            msg = "Task"
            raise NotFoundError(msg, task_id)

        cancelled = await self.queue_client.cancel_task(task_id)
        if not cancelled:
            # Task exists but couldn't be cancelled (running)
            msg = (
                "Task is currently running and cannot be cancelled. Please wait for it to complete."
            )
            raise ValidationError(msg)
        return CancelTaskResponse(status="cancelled")

    async def retry_task(self, task_id: str) -> RetryTaskResponse:
        """Retry a failed task."""
        self._check_available()

        try:
            new_task_id = await self.queue_client.retry_task(task_id)
            if not new_task_id:
                raise NotFoundError("Task", task_id)
            return RetryTaskResponse(new_task_id=new_task_id, original_task_id=task_id)
        except ValueError as e:
            raise ValidationError(str(e)) from e

    async def cancel_by_metadata(
        self, metadata: dict[str, Any], queue: str | None = None
    ) -> CancelByMetadataResponse:
        """Cancel all tasks matching metadata."""
        self._check_available()

        cancelled = await self.queue_client.cancel_by_metadata(metadata=metadata, queue=queue)
        return CancelByMetadataResponse(cancelled=cancelled)

    async def cancel_batch(self, task_ids: list[str]) -> CancelBatchResponse:
        """Cancel multiple tasks by ID using fast batch operations."""
        self._check_available()

        if not task_ids:
            return CancelBatchResponse(cancelled_count=0, requested_count=0, failed=[])

        # Use fast batch cancellation
        result = await self.queue_client.cancel_tasks_batch(task_ids)

        return CancelBatchResponse(
            cancelled_count=result["cancelled"],
            requested_count=len(task_ids),
            failed=result["failed"],
        )

    async def cancel_all_tasks(self, queue: str | None = None) -> CancelAllResponse:
        """Cancel all active tasks."""
        self._check_available()

        cancelled = await self.queue_client.cancel_all_tasks(queue=queue)
        return CancelAllResponse(cancelled=cancelled, queue=queue)

    async def clear_task_history(
        self, queue: str | None = None, older_than_hours: int = 0
    ) -> ClearHistoryResponse:
        """Clear completed task history."""
        self._check_available()

        cleared = await self.queue_client.clear_old_completed_tasks(
            queue=queue, older_than_hours=older_than_hours
        )
        return ClearHistoryResponse(cleared=cleared, queue=queue)

    async def get_all_stats(self) -> QueueStatsResponse:
        """Get statistics for all queues."""
        if not self.queue_client.is_available:
            return QueueStatsResponse(queues=[], note="Queue service unavailable")

        stats = await self.queue_client.get_all_stats()
        return QueueStatsResponse(
            queues=stats, note="Queue configuration managed in worker/config.py"
        )

    async def get_queue_stats(self, queue_name: str) -> dict[str, Any]:
        """Get queue statistics."""
        self._check_available()

        return await self.queue_client.get_queue_stats(queue_name)

    def get_health(self) -> QueueHealthResponse:
        """Get queue system health."""
        return QueueHealthResponse(
            status="healthy" if self.queue_client.is_available else "unavailable",
            enabled=self.queue_client.is_enabled,
            connected=self.queue_client.is_available,
            system="valkey",
            note="Workers run in separate container. See worker/config.py for concurrency settings.",
        )

    # ------------------------------------------------------------------
    # Self-healing reconciliation
    # ------------------------------------------------------------------

    async def force_reconcile(self, queue_name: str | None = None) -> dict[str, int]:
        """Trigger an immediate reconciliation pass.

        Counter persistence happens inside reconcile_queue itself, so
        this method just delegates and merges across queues.

        Args:
            queue_name: Specific queue name, or None to reconcile all
                configured queues.

        Returns:
            Merged ReconcileStats as a dict. When the queue client is
            not available, returns zero counters without raising.
        """
        if not self.queue_client.is_available:
            logger.info("force_reconcile_skipped", reason="queue_unavailable")
            return ReconcileStats().to_dict()

        from chaoscypher_core.app_config import get_settings

        settings = get_settings()
        max_tries_map = {
            "llm": settings.retries.llm_worker_max_tries,
            "operations": settings.retries.operations_worker_max_tries,
        }
        timeout_map = {
            "llm": settings.timeouts.llm_worker_default,
            "operations": settings.timeouts.operations_worker_default,
        }

        queues = [queue_name] if queue_name else list(self.queue_client.queues)
        merged = ReconcileStats()
        for q in queues:
            stats = await reconcile_queue(
                client=self.queue_client,
                queue_name=q,
                max_tries=max_tries_map.get(q, 5),
                timeout_seconds=timeout_map.get(q),
            )
            merged.merge(stats)

        return merged.to_dict()

    async def get_recovery_counters(self, queue_name: str) -> dict[str, int]:
        """Read persisted recovery counters for a queue.

        Reads from ``queue:{queue}:recovery_counters`` — a dedicated
        hash separate from the queue monitor's ``queue:{queue}:stats``
        (which is used for LLM token/cost tracking).
        """
        if self.queue_client.client is None:
            return {
                "recovered_orphans": 0,
                "recovered_crashed": 0,
                "failed_unrecoverable": 0,
            }
        stats_key = f"queue:{queue_name}:recovery_counters"
        # redis-py stubs widen hgetall() to Awaitable | dict — narrow with cast.
        raw_awaitable = cast("Any", self.queue_client.client.hgetall(stats_key))
        raw = await raw_awaitable
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (raw or {}).items()
        }
        return {
            "recovered_orphans": int(decoded.get("recovered_orphans", 0)),
            "recovered_crashed": int(decoded.get("recovered_crashed", 0)),
            "failed_unrecoverable": int(decoded.get("failed_unrecoverable", 0)),
        }
