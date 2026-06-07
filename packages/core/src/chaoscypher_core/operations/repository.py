# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Operations Repository - queue integration powered by Valkey.

Repository pattern for async operations queue management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import Settings

logger = structlog.get_logger(__name__)


class OperationsRepository:
    """Repository for queuing non-LLM operations.

    Provides data access layer for async task queue operations.
    Uses Valkey queue for background task processing.
    """

    def __init__(
        self, graph_repository: GraphRepository | None = None, settings: Settings | None = None
    ) -> None:
        """Initialize operations repository.

        Args:
            graph_repository: Optional GraphRepository for graph operations
            settings: Optional settings for configuration

        """
        self.graph_repository = graph_repository
        self.settings = settings

        logger.info("operations_repository_initialized")

    # ------------------------------------------------------------------
    # Core interface method - required by WorkflowExecutionService
    # ------------------------------------------------------------------
    async def enqueue_operation(
        self,
        operation_type: str,
        task_id: str,
        data: dict[str, Any],
        priority: int = 50,
        *,
        database_name: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Enqueue an operation task (Core interface).

        This method provides the interface expected by Core's WorkflowExecutionService.
        It maps operation types to the appropriate queue operations.

        Args:
            operation_type: Type of operation (e.g., "execute_workflow").
            task_id: Unique task ID for tracking.
            data: Operation-specific data.
            priority: Task priority (0-100, higher = more priority).
            database_name: Target database for scoping cancel-by-metadata.
                Falls back to ``self.settings.current_database`` when not
                supplied — required when settings are unavailable.
            extra_metadata: Extra keys to merge into the task metadata
                (``user_id``, ``workflow_id``, ``chat_id``, etc.).

        Returns:
            Task ID for tracking.

        """
        resolved_db = database_name
        if resolved_db is None and self.settings is not None:
            resolved_db = self.settings.current_database
        metadata: dict[str, Any] = {
            "task_id": task_id,
            "operation_type": operation_type,
        }
        if resolved_db is not None:
            metadata["database_name"] = resolved_db
        if extra_metadata:
            metadata.update(extra_metadata)
        metadata["task_id"] = task_id
        metadata["operation_type"] = operation_type
        if resolved_db is not None:
            metadata["database_name"] = resolved_db
        return await queue_client.enqueue_task(
            queue=QUEUE_OPERATIONS,
            operation=operation_type,
            data=data,
            priority=priority,
            metadata=metadata,
        )

    async def abort_operation(self, execution_id: str) -> bool:
        """Abort a queued or running operation by execution ID.

        ``enqueue_operation`` stamps ``task_id: <execution_id>`` into the
        Valkey task metadata; we look it up by that and route through
        ``cancel_by_metadata`` so both queued and in-flight tasks are
        handled (queued → removed; running → cancel flag set, worker stops
        on the next batch boundary and releases its LLM slot).

        Args:
            execution_id: ID of the operation to abort (matched against
                ``metadata.task_id`` on queued operations tasks).

        Returns:
            True if at least one task was found and cancelled; False if
            no live task matched the execution_id.
        """
        cancelled = await queue_client.cancel_by_metadata(
            metadata={"task_id": execution_id},
            queue=QUEUE_OPERATIONS,
        )
        return cancelled > 0
