# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Execution Repository: Data access layer for workflow execution records.

This repository separates SQL operations from business logic, following the
Repository Pattern for better testability and maintainability.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import WorkflowExecution
from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

logger = structlog.get_logger(__name__)


class WorkflowExecutionRepository:
    """Repository for workflow execution data access.

    Takes a connected ``SqliteAdapter`` and wraps every write in
    ``adapter.transaction()``. The transaction context is authoritative
    for commit-on-success / rollback-on-exception; individual methods
    never call ``session.commit()`` or ``session.maybe_commit()``.
    """

    def __init__(self, adapter: SqliteAdapter):
        """Initialize repository.

        Args:
            adapter: Connected ``SqliteAdapter`` instance whose
                ``session`` will be used for all operations.

        """
        self.adapter = adapter

    # ========================================================================
    # Workflow Execution Operations
    # ========================================================================

    def create_execution(self, execution_data: dict[str, Any]) -> None:
        """Create workflow execution record.

        Args:
            execution_data: Execution data including id, workflow_id, triggered_by, etc.

        """
        with self.adapter.transaction():
            session = self.adapter.session
            assert session is not None
            execution = WorkflowExecution(
                id=execution_data["id"],
                workflow_id=execution_data["workflow_id"],
                triggered_by=execution_data["triggered_by"],
                trigger_id=execution_data.get("trigger_id"),
                parent_execution_id=execution_data.get("parent_execution_id"),
                inputs=execution_data["inputs"],
                status=execution_data["status"],
                created_at=execution_data.get("created_at", datetime.now(UTC)),
            )
            session.add(execution)

    def update_status(self, execution_id: str, status: str) -> None:
        """Update execution status.

        Args:
            execution_id: Execution ID
            status: New status

        """
        with self.adapter.transaction():
            session = self.adapter.session
            assert session is not None
            statement = select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            execution = session.exec(statement).first()

            if execution:
                execution.status = status

                if status == WorkflowExecutionStatus.RUNNING and not execution.started_at:
                    execution.started_at = datetime.now(UTC)

                session.add(execution)

    def finalize_execution(
        self,
        execution_id: str,
        status: str,
        outputs: dict[str, Any] | None,
        error_message: str | None,
        failed_step_id: str | None,
        duration_ms: int,
    ) -> None:
        """Finalize an execution atomically.

        Writes status + outputs + error_message + failed_step_id +
        completed_at + duration_ms in a single session scope so a crash
        between fields cannot leave inconsistent state.

        Args:
            execution_id: Execution ID
            status: Final status (completed, failed, cancelled)
            outputs: Outputs dict (for completed) or None (for failed/cancelled)
            error_message: Error string (for failed) or None
            failed_step_id: ID of the failed step (for failed) or None
            duration_ms: Total duration in milliseconds

        """
        with self.adapter.transaction():
            session = self.adapter.session
            assert session is not None
            statement = select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            execution = session.exec(statement).first()
            if not execution:
                logger.warning("finalize_execution_not_found", execution_id=execution_id)
                return
            execution.status = status
            if outputs is not None:
                execution.outputs = outputs
            if error_message is not None:
                execution.error_message = error_message
            if failed_step_id is not None:
                execution.failed_step_id = failed_step_id
            execution.completed_at = datetime.now(UTC)
            execution.duration_ms = duration_ms
            session.add(execution)
            session.maybe_commit()

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        """Get execution details.

        Args:
            execution_id: Execution ID

        Returns:
            Execution dict or None if not found

        """
        session = self.adapter.session
        assert session is not None
        statement = select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
        execution = session.exec(statement).first()

        if execution:
            return execution.model_dump()
        return None
