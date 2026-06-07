# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Execution Mixin - SQLite implementation of WorkflowExecutionStorageProtocol.

Provides workflow execution tracking functionality for SqliteAdapter following
the mixin pattern. Handles both workflow-level and step-level execution records.

Architecture:
    - Implements WorkflowExecutionStorageProtocol
    - Uses self.session (provided by SqliteAdapter)
    - Returns/accepts dicts (not entities) for framework independence
    - Uses plain string status values (not Enums)

Example:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    adapter = SqliteAdapter(database_path="data/app.db")
    adapter.connect()

    # Create execution
    execution = adapter.create_execution({
        "id": "exec_123",
        "workflow_id": "wf_456",
        "triggered_by": "user",
        "inputs": {"param": "value"},
        "status": "pending"
    })

    # Update status
    adapter.update_status("exec_123", "running")

    # Complete execution
    adapter.complete_execution("exec_123", {"result": "success"}, 5000)

"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import load_only
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import WorkflowExecution, WorkflowStepExecution
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.ports.storage_workflow_executions import WorkflowExecutionStorageProtocol


logger = structlog.get_logger(__name__)


class WorkflowExecutionsMixin(SqliteMixinBase, WorkflowExecutionStorageProtocol):
    """Mixin implementing WorkflowExecutionStorageProtocol for SQLite storage.

    Provides complete workflow execution tracking including workflow-level
    executions and per-step execution records.

    Attributes:
        session: SQLModel session (provided by SqliteAdapter)
        _ensure_connected: Method from SqliteAdapter ensuring connection
        _entity_to_dict: Helper method converting entity to dict
        _entities_to_dicts: Helper method converting entities to dicts

    Note:
        All methods assume session is managed by parent SqliteAdapter.
        Status values are plain strings for framework independence.

    """

    # ========================================================================
    # Workflow Execution Operations
    # ========================================================================

    def create_execution(self, execution_data: dict[str, Any]) -> dict[str, Any]:
        """Create workflow execution record.

        Args:
            execution_data: Dict containing:
                - id: str - Execution ID
                - workflow_id: str - Workflow ID
                - triggered_by: str - Trigger source
                - trigger_id: Optional[str] - Trigger ID if applicable
                - parent_execution_id: Optional[str] - Parent execution if nested
                - inputs: Dict[str, Any] - Execution inputs
                - status: str - Initial status (typically "pending")
                - created_at: Optional[datetime] - Creation timestamp

        Returns:
            Created execution as dict with all fields

        Example:
            >>> execution = adapter.create_execution({
            ...     "id": "exec_123",
            ...     "workflow_id": "wf_456",
            ...     "triggered_by": "user",
            ...     "inputs": {"query": "test"},
            ...     "status": "pending"
            ... })
            >>> print(execution["id"])
            "exec_123"

        """
        self._ensure_connected()
        execution = WorkflowExecution(**execution_data)
        self.session.add(execution)
        self._maybe_commit()
        self.session.refresh(execution)
        return self._entity_to_dict(execution)

    def update_status(self, execution_id: str, status: str) -> None:
        """Update execution status.

        Args:
            execution_id: Execution ID
            status: New status ("pending", "running", "completed", "failed", "cancelled")

        Raises:
            NotFoundError: If execution not found

        Note:
            If status is "running" and started_at is None, sets started_at to now.

        Example:
            >>> adapter.update_status("exec_123", "running")
            >>> adapter.update_status("exec_123", "completed")

        """
        self._ensure_connected()
        execution = self.session.get(WorkflowExecution, execution_id)
        if not execution:
            msg = "WorkflowExecution"
            raise NotFoundError(msg, execution_id)

        execution.status = status

        # Auto-set started_at when execution starts
        if status == "running" and not execution.started_at:
            execution.started_at = datetime.now(UTC)

        self.session.add(execution)
        self._maybe_commit()

    def update_current_step(self, execution_id: str, step_id: str) -> None:
        """Update currently executing step.

        Args:
            execution_id: Execution ID
            step_id: Step ID currently being executed

        Raises:
            NotFoundError: If execution not found

        Example:
            >>> adapter.update_current_step("exec_123", "step_456")

        """
        self._ensure_connected()
        execution = self.session.get(WorkflowExecution, execution_id)
        if not execution:
            msg = "WorkflowExecution"
            raise NotFoundError(msg, execution_id)

        execution.current_step_id = step_id
        self.session.add(execution)
        self._maybe_commit()

    def complete_execution(
        self, execution_id: str, outputs: dict[str, Any], duration_ms: int
    ) -> None:
        """Mark execution as completed.

        Args:
            execution_id: Execution ID
            outputs: Execution outputs
            duration_ms: Total execution duration in milliseconds

        Raises:
            NotFoundError: If execution not found

        Note:
            Sets status="completed", completed_at=now, outputs, duration_ms.

        Example:
            >>> adapter.complete_execution(
            ...     "exec_123",
            ...     {"result": "success", "entities": 42},
            ...     5000
            ... )

        """
        self._ensure_connected()
        execution = self.session.get(WorkflowExecution, execution_id)
        if not execution:
            msg = "WorkflowExecution"
            raise NotFoundError(msg, execution_id)

        execution.status = "completed"
        execution.outputs = outputs
        execution.completed_at = datetime.now(UTC)
        execution.duration_ms = duration_ms

        self.session.add(execution)
        self._maybe_commit()

    def fail_execution(
        self, execution_id: str, error_message: str, failed_step_id: str | None, duration_ms: int
    ) -> None:
        """Mark execution as failed.

        Args:
            execution_id: Execution ID
            error_message: Error message describing failure
            failed_step_id: ID of step that failed (None if failed before first step)
            duration_ms: Duration until failure in milliseconds

        Raises:
            NotFoundError: If execution not found

        Note:
            Sets status="failed", error_message, failed_step_id, completed_at=now, duration_ms.

        Example:
            >>> adapter.fail_execution(
            ...     "exec_123",
            ...     "Connection timeout",
            ...     "step_456",
            ...     3000
            ... )

        """
        self._ensure_connected()
        execution = self.session.get(WorkflowExecution, execution_id)
        if not execution:
            msg = "WorkflowExecution"
            raise NotFoundError(msg, execution_id)

        execution.status = "failed"
        execution.error_message = error_message
        execution.failed_step_id = failed_step_id
        execution.completed_at = datetime.now(UTC)
        execution.duration_ms = duration_ms

        self.session.add(execution)
        self._maybe_commit()

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        """Get execution details by ID.

        Args:
            execution_id: Execution ID

        Returns:
            Execution dict or None if not found

        Example:
            >>> execution = adapter.get_execution("exec_123")
            >>> if execution:
            ...     print(f"Status: {execution['status']}")

        """
        self._ensure_connected()
        execution = self.session.get(WorkflowExecution, execution_id)
        return self._entity_to_dict(execution) if execution else None

    def get_workflow_executions(self, workflow_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get execution history for a workflow.

        Uses load_only() to prevent lazy loading of the step_executions
        relationship which would trigger N+1 queries in list views.

        Args:
            workflow_id: Workflow ID
            limit: Maximum number of executions to return (default: 100)

        Returns:
            List of execution dicts, ordered by created_at desc (most recent first)

        Example:
            >>> executions = adapter.get_workflow_executions("wf_456", limit=10)
            >>> for exec in executions:
            ...     print(f"{exec['id']}: {exec['status']}")

        """
        self._ensure_connected()
        stmt = (
            select(WorkflowExecution)
            .options(
                load_only(
                    WorkflowExecution.id,
                    WorkflowExecution.workflow_id,
                    WorkflowExecution.triggered_by,
                    WorkflowExecution.trigger_id,
                    WorkflowExecution.parent_execution_id,
                    WorkflowExecution.inputs,
                    WorkflowExecution.outputs,
                    WorkflowExecution.status,
                    WorkflowExecution.current_step_id,
                    WorkflowExecution.failed_step_id,
                    WorkflowExecution.error_message,
                    WorkflowExecution.duration_ms,
                    WorkflowExecution.created_at,
                    WorkflowExecution.started_at,
                    WorkflowExecution.completed_at,
                    # EXCLUDE: step_executions (relationship — prevents N+1 queries)
                )
            )
            .where(WorkflowExecution.workflow_id == workflow_id)
            .order_by(WorkflowExecution.created_at.desc())
            .limit(limit)
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def list_active_executions(self, workflow_id: str) -> list[dict[str, Any]]:
        """Return executions for this workflow that are pending/queued/running.

        On SQLite the write serialization via the single writer lock
        provides equivalent guarantees to FOR UPDATE.

        Args:
            workflow_id: Workflow ID

        Returns:
            List of active execution dicts.

        """
        self._ensure_connected()
        statement = select(WorkflowExecution).where(
            WorkflowExecution.workflow_id == workflow_id,
            WorkflowExecution.status.in_(["pending", "queued", "running"]),
        )
        rows = self.session.exec(statement).all()
        return self._entities_to_dicts(rows)

    # ========================================================================
    # Step Execution Operations
    # ========================================================================

    def create_step_execution(
        self, step_execution_id: str, execution_id: str, step_id: str
    ) -> dict[str, Any]:
        """Create step execution record.

        Args:
            step_execution_id: Step execution ID
            execution_id: Parent execution ID
            step_id: Step ID from workflow definition

        Returns:
            Created step execution as dict

        Note:
            Initial status is "pending", inputs={} (will be updated later).

        Example:
            >>> step = adapter.create_step_execution(
            ...     "step_exec_789",
            ...     "exec_123",
            ...     "step_456"
            ... )
            >>> print(step["status"])
            "pending"

        """
        self._ensure_connected()
        step_execution = WorkflowStepExecution(
            id=step_execution_id,
            execution_id=execution_id,
            step_id=step_id,
            inputs={},  # Will be updated with actual inputs later
            status="pending",
            created_at=datetime.now(UTC),
        )
        self.session.add(step_execution)
        self._maybe_commit()
        self.session.refresh(step_execution)
        return self._entity_to_dict(step_execution)

    def update_step_status(self, step_execution_id: str, status: str) -> None:
        """Update step execution status.

        Args:
            step_execution_id: Step execution ID
            status: New status ("pending", "running", "completed", "failed", "skipped")

        Raises:
            NotFoundError: If step execution not found

        Note:
            If status is "running" and started_at is None, sets started_at to now.

        Example:
            >>> adapter.update_step_status("step_exec_789", "running")
            >>> adapter.update_step_status("step_exec_789", "completed")

        """
        self._ensure_connected()
        step_execution = self.session.get(WorkflowStepExecution, step_execution_id)
        if not step_execution:
            msg = "WorkflowStepExecution"
            raise NotFoundError(msg, step_execution_id)

        step_execution.status = status

        # Auto-set started_at when step starts
        if status == "running" and not step_execution.started_at:
            step_execution.started_at = datetime.now(UTC)

        self.session.add(step_execution)
        self._maybe_commit()

    def complete_step_execution(
        self, step_execution_id: str, outputs: dict[str, Any], duration_ms: int
    ) -> None:
        """Mark step execution as completed.

        Args:
            step_execution_id: Step execution ID
            outputs: Step outputs
            duration_ms: Step execution duration in milliseconds

        Raises:
            NotFoundError: If step execution not found

        Note:
            Sets status="completed", outputs, completed_at=now, duration_ms.

        Example:
            >>> adapter.complete_step_execution(
            ...     "step_exec_789",
            ...     {"extracted_entities": 10},
            ...     2000
            ... )

        """
        self._ensure_connected()
        step_execution = self.session.get(WorkflowStepExecution, step_execution_id)
        if not step_execution:
            msg = "WorkflowStepExecution"
            raise NotFoundError(msg, step_execution_id)

        step_execution.status = "completed"
        step_execution.outputs = outputs
        step_execution.completed_at = datetime.now(UTC)
        step_execution.duration_ms = duration_ms

        self.session.add(step_execution)
        self._maybe_commit()

    def fail_step_execution(
        self, step_execution_id: str, error_message: str, duration_ms: int
    ) -> None:
        """Mark step execution as failed.

        Args:
            step_execution_id: Step execution ID
            error_message: Error message describing failure
            duration_ms: Duration until failure in milliseconds

        Raises:
            NotFoundError: If step execution not found

        Note:
            Sets status="failed", error_message, completed_at=now, duration_ms.

        Example:
            >>> adapter.fail_step_execution(
            ...     "step_exec_789",
            ...     "Invalid input schema",
            ...     1500
            ... )

        """
        self._ensure_connected()
        step_execution = self.session.get(WorkflowStepExecution, step_execution_id)
        if not step_execution:
            msg = "WorkflowStepExecution"
            raise NotFoundError(msg, step_execution_id)

        step_execution.status = "failed"
        step_execution.error_message = error_message
        step_execution.completed_at = datetime.now(UTC)
        step_execution.duration_ms = duration_ms

        self.session.add(step_execution)
        self._maybe_commit()

    def get_step_executions(self, execution_id: str) -> list[dict[str, Any]]:
        """Get step executions for a workflow execution.

        Uses load_only() to prevent lazy loading of the execution
        relationship which would trigger an additional query per step.

        Args:
            execution_id: Execution ID

        Returns:
            List of step execution dicts, ordered by created_at asc (execution order)

        Example:
            >>> steps = adapter.get_step_executions("exec_123")
            >>> for step in steps:
            ...     print(f"{step['step_id']}: {step['status']}")

        """
        self._ensure_connected()
        stmt = (
            select(WorkflowStepExecution)
            .options(
                load_only(
                    WorkflowStepExecution.id,
                    WorkflowStepExecution.execution_id,
                    WorkflowStepExecution.step_id,
                    WorkflowStepExecution.inputs,
                    WorkflowStepExecution.outputs,
                    WorkflowStepExecution.status,
                    WorkflowStepExecution.error_message,
                    WorkflowStepExecution.duration_ms,
                    WorkflowStepExecution.created_at,
                    WorkflowStepExecution.started_at,
                    WorkflowStepExecution.completed_at,
                    # EXCLUDE: execution (relationship — prevents extra query per step)
                )
            )
            .where(WorkflowStepExecution.execution_id == execution_id)
            .order_by(WorkflowStepExecution.created_at.asc())
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 11).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def clear_all_workflow_executions(self) -> int:
        """Delete every WorkflowExecution row across databases."""
        self._ensure_connected()
        result = self.session.exec(delete(WorkflowExecution))
        self._maybe_commit()
        return int(result.rowcount or 0)


__all__ = ["WorkflowExecutionsMixin"]
