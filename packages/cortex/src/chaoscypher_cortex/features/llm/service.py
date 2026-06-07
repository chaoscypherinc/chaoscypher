# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Service.

Business logic for LLM queue monitoring and management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.llm import clear_llm_semaphore_waiting_queues
from chaoscypher_core.exceptions import (
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from chaoscypher_cortex.features.llm.models import (
    CancelAllTasksResponse,
    ClearSemaphoreResponse,
    LLMStatsResponse,
    LLMTasksResponse,
    LLMTaskStatusResponse,
)


if TYPE_CHECKING:
    from chaoscypher_core.llm_queue.queue_service import LLMQueueService


logger = structlog.get_logger(__name__)


class LLMService:
    """Service for LLM queue operations (wraps existing LLM service)."""

    def __init__(self, llm_manager: LLMQueueService | None) -> None:
        """Initialize LLM service.

        Args:
            llm_manager: LLM manager instance

        """
        self.llm_manager = llm_manager

    def _check_available(self) -> LLMQueueService:
        """Check if LLM service is available and return it."""
        if not self.llm_manager:
            msg = "LLMQueue"
            raise ExternalServiceError(msg, "Service not available")
        return self.llm_manager

    async def get_stats(self) -> LLMStatsResponse:
        """Get LLM queue stats."""
        manager = self._check_available()
        stats = await manager.get_stats()
        return LLMStatsResponse(data=stats)

    async def clear_stats(self, older_than_hours: int) -> None:
        """Clear LLM queue stats and old completed tasks.

        Args:
            older_than_hours: Clear tasks older than this many hours

        """
        manager = self._check_available()

        # Clear LLM queue stats and old tasks
        await manager.clear_stats(older_than_hours=older_than_hours)

    async def list_current_tasks(self) -> LLMTasksResponse:
        """List currently queued and running tasks."""
        manager = self._check_available()
        tasks = await manager.list_current_tasks()
        return LLMTasksResponse(data=tasks)

    async def get_task_status(self, task_id: str) -> LLMTaskStatusResponse:
        """Get status of a specific queued task."""
        manager = self._check_available()
        task_status = await manager.get_task_status(task_id)
        if not task_status:
            msg = "LLMTask"
            raise NotFoundError(msg, task_id)
        return LLMTaskStatusResponse(data=task_status)

    async def cancel_task(self, task_id: str) -> None:
        """Cancel a queued or running task."""
        manager = self._check_available()
        cancelled = await manager.cancel_task(task_id)
        if not cancelled:
            msg = "Task could not be cancelled (not found or already completed)"
            raise ValidationError(msg)

    async def cancel_all_tasks(self) -> CancelAllTasksResponse:
        """Cancel all queued and running tasks."""
        manager = self._check_available()
        result = await manager.cancel_all_tasks()
        return CancelAllTasksResponse(data=result)

    async def clear_semaphore(self) -> ClearSemaphoreResponse:
        """Clear all waiting tasks from the LLM semaphore queues.

        This is useful when Valkey queues are cleared but the semaphore still has
        orphaned waiters that will never complete.
        """
        result = await clear_llm_semaphore_waiting_queues()
        return ClearSemaphoreResponse(data=result)
