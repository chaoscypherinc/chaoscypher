# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""WorkflowExecutionStorageProtocol — storage contract for workflow execution tracking.

Split from the legacy ``ports/storage.py`` god file on 2026-04-23.
Implemented by ``chaoscypher_core.adapters.sqlite.mixins.workflow_executions.WorkflowExecutionsMixin``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WorkflowExecutionStorageProtocol(Protocol):
    """Storage protocol for workflow execution tracking operations.

    Handles CRUD for:
    - Workflow execution records (status, timing, outputs)
    - Step execution records (per-step tracking)

    Separated from WorkflowStorageProtocol per Interface Segregation Principle.
    Workflow definitions are stable (rarely change), but executions are frequent
    and runtime-focused.

    Note:
        All status values are plain strings (framework-agnostic).
        Backend DTOs may use Enums, but storage layer uses strings.

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
                - triggered_by: str - Trigger source ("user", "schedule", "trigger")
                - trigger_id: Optional[str] - Trigger ID if triggered by trigger
                - parent_execution_id: Optional[str] - Parent execution if nested
                - inputs: Dict[str, Any] - Execution inputs
                - status: str - Initial status (typically "pending")
                - created_at: Optional[datetime] - Creation timestamp

        Returns:
            Created execution as dict (with generated fields)

        """
        ...

    def update_status(self, execution_id: str, status: str) -> None:
        """Update execution status.

        Args:
            execution_id: Execution ID
            status: New status ("pending", "running", "completed", "failed", "cancelled")

        Note:
            If status is "running" and started_at is None, sets started_at to now.

        """
        ...

    def update_current_step(self, execution_id: str, step_id: str) -> None:
        """Update currently executing step.

        Args:
            execution_id: Execution ID
            step_id: Step ID currently being executed

        """
        ...

    def complete_execution(
        self, execution_id: str, outputs: dict[str, Any], duration_ms: int
    ) -> None:
        """Mark execution as completed.

        Args:
            execution_id: Execution ID
            outputs: Execution outputs
            duration_ms: Total execution duration in milliseconds

        Note:
            Sets status="completed", completed_at=now, outputs, duration_ms.

        """
        ...

    def fail_execution(
        self, execution_id: str, error_message: str, failed_step_id: str | None, duration_ms: int
    ) -> None:
        """Mark execution as failed.

        Args:
            execution_id: Execution ID
            error_message: Error message describing failure
            failed_step_id: ID of step that failed (None if failed before first step)
            duration_ms: Duration until failure in milliseconds

        Note:
            Sets status="failed", error_message, failed_step_id, completed_at=now, duration_ms.

        """
        ...

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        """Get execution details by ID.

        Args:
            execution_id: Execution ID

        Returns:
            Execution dict or None if not found

        """
        ...

    def get_workflow_executions(self, workflow_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get execution history for a workflow.

        Args:
            workflow_id: Workflow ID
            limit: Maximum number of executions to return (default: 100)

        Returns:
            List of execution dicts, ordered by created_at desc (most recent first)

        """
        ...

    def list_active_executions(self, workflow_id: str) -> list[dict[str, Any]]:
        """Return executions with status in {pending, queued, running} for a workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            List of active execution dicts (empty if none).

        """
        ...

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

        """
        ...

    def update_step_status(self, step_execution_id: str, status: str) -> None:
        """Update step execution status.

        Args:
            step_execution_id: Step execution ID
            status: New status ("pending", "running", "completed", "failed", "skipped")

        Note:
            If status is "running" and started_at is None, sets started_at to now.

        """
        ...

    def complete_step_execution(
        self, step_execution_id: str, outputs: dict[str, Any], duration_ms: int
    ) -> None:
        """Mark step execution as completed.

        Args:
            step_execution_id: Step execution ID
            outputs: Step outputs
            duration_ms: Step execution duration in milliseconds

        Note:
            Sets status="completed", outputs, completed_at=now, duration_ms.

        """
        ...

    def fail_step_execution(
        self, step_execution_id: str, error_message: str, duration_ms: int
    ) -> None:
        """Mark step execution as failed.

        Args:
            step_execution_id: Step execution ID
            error_message: Error message describing failure
            duration_ms: Duration until failure in milliseconds

        Note:
            Sets status="failed", error_message, completed_at=now, duration_ms.

        """
        ...

    def get_step_executions(self, execution_id: str) -> list[dict[str, Any]]:
        """Get step executions for a workflow execution.

        Args:
            execution_id: Execution ID

        Returns:
            List of step execution dicts, ordered by created_at asc (execution order)

        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 11).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def clear_all_workflow_executions(self) -> int:
        """Delete every WorkflowExecution row across databases. Returns count."""
        ...
