# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Stats Tracker: Tracks trigger execution statistics.

This service provides:
- Per-trigger execution history
- Total executions, successes, failures
- Execution time tracking
- Configurable history limit (circular buffer per trigger)
"""

from collections import defaultdict, deque
from dataclasses import asdict
from datetime import UTC, datetime

import structlog

from chaoscypher_core.services.workflows.triggers.models import (
    TriggerExecution,
    TriggerExecutionStatus,
    TriggerStats,
)


logger = structlog.get_logger(__name__)


class TriggerStatsTracker:
    """Tracks trigger execution statistics."""

    def __init__(self, history_limit: int = 100):
        """Initialize the trigger stats tracker.

        Args:
            history_limit: Maximum number of execution records to keep per trigger

        """
        self.history_limit = history_limit

        # Per-trigger execution history (circular buffer)
        self.trigger_history: dict[str, deque[TriggerExecution]] = defaultdict(
            lambda: deque(maxlen=history_limit)
        )

        # Per-trigger statistics
        self.trigger_stats: dict[str, TriggerStats] = {}

        # Track execution times for average calculation (per trigger)
        self.execution_times: dict[str, list[float]] = defaultdict(list)

        logger.info("trigger_stats_tracker_initialized", history_limit=history_limit)

    def record_execution(
        self,
        execution_id: str,
        trigger_id: str,
        trigger_name: str,
        workflow_id: str,
        workflow_name: str,
        event_source: str,
        success: bool,
        execution_time: float,
        error: str | None = None,
    ) -> None:
        """Record a trigger execution.

        Args:
            execution_id: Unique execution ID
            trigger_id: ID of the trigger
            trigger_name: Name of the trigger
            workflow_id: ID of the workflow that was executed
            workflow_name: Name of the workflow
            event_source: Event that triggered the execution
            success: Whether the execution succeeded
            execution_time: Time taken to execute (seconds)
            error: Error message if failed

        """
        # Create execution record
        execution = TriggerExecution(
            execution_id=execution_id,
            trigger_id=trigger_id,
            trigger_name=trigger_name,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status=TriggerExecutionStatus.SUCCESS if success else TriggerExecutionStatus.FAILED,
            event_source=event_source,
            fired_at=datetime.now(UTC),
            execution_time=execution_time,
            error=error,
        )

        # Add to trigger history (deque automatically evicts oldest when limit reached)
        self.trigger_history[trigger_id].append(execution)

        # Initialize stats if needed
        if trigger_id not in self.trigger_stats:
            self.trigger_stats[trigger_id] = TriggerStats(trigger_id=trigger_id)

        stats = self.trigger_stats[trigger_id]

        # Update stats
        stats.total_executions += 1
        if success:
            stats.successful += 1
            self.execution_times[trigger_id].append(execution_time)

            # Update average execution time
            stats.avg_execution_time = sum(self.execution_times[trigger_id]) / len(
                self.execution_times[trigger_id]
            )
        else:
            stats.failed += 1

        # Update success rate (as decimal 0-1, not percentage)
        if stats.total_executions > 0:
            stats.success_rate = stats.successful / stats.total_executions

        logger.debug(
            "trigger_execution_recorded",
            trigger_id=trigger_id,
            trigger_name=trigger_name,
            success=success,
            execution_time=round(execution_time, 2),
        )

    def get_all_stats(self) -> dict[str, dict]:
        """Get statistics for all triggers.

        Returns:
            Dictionary mapping trigger_id to stats

        """
        return {trigger_id: asdict(stats) for trigger_id, stats in self.trigger_stats.items()}
