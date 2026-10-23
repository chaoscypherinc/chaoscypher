# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Execution Repository: Data access layer for workflow execution records.

This repository separates SQL operations from business logic, following the
Repository Pattern for better testability and maintainability.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, update
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import WorkflowExecution
from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

logger = structlog.get_logger(__name__)

_TERMINAL_STATUSES = (
    WorkflowExecutionStatus.COMPLETED,
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.CANCELLED,
)


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

    def update_status(self, execution_id: str, status: str) -> bool:
        """Update execution status unless the row already reached a terminal state.

        The write is a single conditional ``UPDATE`` so a cancel that lands
        between the worker's pickup read and this write (Cortex stamps the
        row ``cancelled``) is never resurrected to ``running``.

        Args:
            execution_id: Execution ID
            status: New status

        Returns:
            True if the row was updated; False if it is missing or terminal.

        """
        values: dict[str, Any] = {"status": status}
        if status == WorkflowExecutionStatus.RUNNING:
            values["started_at"] = func.coalesce(WorkflowExecution.started_at, datetime.now(UTC))
        with self.adapter.transaction():
            session = self.adapter.session
            assert session is not None
            result = session.exec(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id)  # type: ignore[arg-type]
                .where(WorkflowExecution.status.not_in(_TERMINAL_STATUSES))  # type: ignore[attr-defined]
                .values(**values)
            )
        return bool(result.rowcount)

    def finalize_execution(
        self,
        execution_id: str,
        status: str,
        outputs: dict[str, Any] | None,
        error_message: str | None,
        failed_step_id: str | None,
        duration_ms: int,
    ) -> bool:
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

        Returns:
            True if the row was finalized; False if it is missing or was
            already cancelled by the user.

        """
        values: dict[str, Any] = {
            "status": status,
            "completed_at": datetime.now(UTC),
            "duration_ms": duration_ms,
        }
        if outputs is not None:
            values["outputs"] = outputs
        if error_message is not None:
            values["error_message"] = error_message
        if failed_step_id is not None:
            values["failed_step_id"] = failed_step_id
        with self.adapter.transaction():
            session = self.adapter.session
            assert session is not None
            # Conditional UPDATE: a user cancel (Cortex writes ``cancelled``)
            # is final — the worker finishing afterwards must not overwrite it.
            result = session.exec(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id)  # type: ignore[arg-type]
                .where(WorkflowExecution.status != WorkflowExecutionStatus.CANCELLED)  # type: ignore[arg-type]
                .values(**values)
            )
        if not result.rowcount:
            logger.warning(
                "finalize_execution_skipped",
                execution_id=execution_id,
                reason="not_found_or_cancelled",
            )
            return False
        return True

    def get_status(self, execution_id: str) -> str | None:
        """Read the execution's current status straight from the database.

        A column-only ``SELECT`` bypasses the session identity map, so a
        status written by another process (a Cortex cancel) is seen on the
        next poll instead of the cached entity's stale value.

        Args:
            execution_id: Execution ID

        Returns:
            The status string, or None if the row does not exist.

        """
        session = self.adapter.session
        assert session is not None
        return session.exec(
            select(WorkflowExecution.status).where(WorkflowExecution.id == execution_id)
        ).first()

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
