# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Execution Service.

Business logic for workflow execution operations.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import (
    AuthorizationError,
    NotFoundError,
    OperationError,
    ValidationError,
)
from chaoscypher_core.models import UserPrincipal
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_workflow_executions import WorkflowExecutionStorageProtocol
    from chaoscypher_core.ports.storage_workflows import WorkflowStorageProtocol

logger = structlog.get_logger(__name__)


def _normalize_user(user: Any) -> UserPrincipal | None:
    """Normalize heterogeneous user shapes to UserPrincipal.

    Accepts dict (TypedDict), object with .id/.is_admin, or None.
    Raises TypeError for anything else.

    Args:
        user: Input user (dict, object with attributes, or None).

    Returns:
        UserPrincipal instance, or None if input was None.

    Raises:
        TypeError: If ``user`` is neither dict, attribute-bearing object,
            nor None.

    """
    if user is None:
        return None
    if isinstance(user, dict):
        return UserPrincipal(
            id=user.get("id"),
            is_admin=bool(user.get("is_admin", False)),
        )
    if hasattr(user, "id"):
        return UserPrincipal(
            id=getattr(user, "id", None),
            is_admin=bool(getattr(user, "is_admin", False)),
        )
    msg = (
        f"user must be a dict, have .id/.is_admin attributes, or be None; got {type(user).__name__}"
    )
    raise TypeError(  # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: caller passed wrong type; never user-reachable
        msg
    )


class WorkflowExecutionService:
    """Service for workflow execution management.

    Uses storage protocols for framework-agnostic execution tracking.
    No longer imports from backend - follows hexagonal architecture.
    """

    def __init__(
        self,
        repository: WorkflowStorageProtocol,
        execution_repository: WorkflowExecutionStorageProtocol,
        operations_service: Any,  # OperationsService type
        stats_max_executions: int = 1000,
    ):
        """Initialize execution service.

        Args:
            repository: WorkflowStorageProtocol for workflow data access
            execution_repository: WorkflowExecutionStorageProtocol for execution tracking
            operations_service: OperationsService for async queueing
            stats_max_executions: Max executions to fetch for statistics calculation

        """
        self.repository = repository
        self.execution_repo = execution_repository
        self.operations_service = operations_service
        self._stats_max_executions = stats_max_executions

    async def execute_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        triggered_by: str = "manual",
        user: Any | None = None,
    ) -> str:
        """Queue workflow execution asynchronously.

        Args:
            workflow_id: Workflow ID to execute
            inputs: Input parameters
            triggered_by: How workflow was triggered
            user: Current user (for access control)

        Returns:
            Execution ID (task ID for queue)

        Raises:
            NotFoundError: If workflow not found
            AuthorizationError: If access denied
            ValidationError: If workflow is not active

        """
        # Check workflow exists and is active
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            msg = f"Workflow {workflow_id} not found"
            raise NotFoundError("Workflow", workflow_id)

        # Check access - user may be a dict (TypedDict) or an object
        principal = _normalize_user(user)
        user_id = principal.id if principal else None
        is_admin = principal.is_admin if principal else False
        workflow_owner = workflow.get("user_id")
        if (
            user
            and user_id is not None
            and user_id > 0
            and not is_admin
            and not workflow.get("is_system")
            and workflow_owner is not None
            and workflow_owner != user_id
        ):
            msg = "Access denied to this workflow"
            raise AuthorizationError(msg)

        if not workflow.get("is_active"):
            msg = f"Workflow {workflow_id} is not active"
            raise ValidationError(msg)

        # AI-tool-exposed workflows require workflow:execute permission
        if workflow.get("expose_as_ai_tool") and triggered_by == "ai_tool" and not is_admin:
            msg = "workflow:execute permission required for AI-tool-exposed workflows"
            raise AuthorizationError(msg, required_permission="workflow:execute")

        # Enforce allow_parallel_execution=False
        if not workflow.get("allow_parallel_execution", True):
            active = self.execution_repo.list_active_executions(workflow_id)
            if active:
                from chaoscypher_core.exceptions import WorkflowBusyError

                raise WorkflowBusyError(
                    workflow_id=workflow_id,
                    active_execution_id=active[0].get("id", "<unknown>"),
                )

        # Queue the workflow execution via operations service
        execution_id = generate_id()

        # Create execution record BEFORE queueing (so frontend can poll immediately)
        from datetime import UTC, datetime

        self.execution_repo.create_execution(
            {
                "id": execution_id,
                "workflow_id": workflow_id,
                "triggered_by": triggered_by,
                "inputs": inputs,
                "status": "pending",
                "created_at": datetime.now(UTC),
            }
        )

        # Enqueue workflow execution task with error handling
        try:
            await self.operations_service.enqueue_operation(
                operation_type="execute_workflow",
                task_id=execution_id,
                data={
                    "workflow_id": workflow_id,
                    "inputs": inputs,
                    "triggered_by": triggered_by,
                    "execution_id": execution_id,  # Pass existing execution ID
                },
            )
        except Exception as e:
            logger.exception(
                "workflow_execution_enqueue_failed",
                execution_id=execution_id,
                workflow_id=workflow_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            # Update existing execution record to failed state
            self.execution_repo.fail_execution(
                execution_id, f"Failed to queue execution: {e!s}", None, 0
            )
            msg = f"Failed to queue workflow execution. Queue service may be unavailable: {e!s}"
            raise OperationError(msg) from e

        return execution_id

    def get_executions(
        self,
        workflow_id: str,
        limit: int = 10,
        skip: int = 0,
        status_filter: str | None = None,
        user: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Get execution history for a workflow.

        Args:
            workflow_id: Workflow ID
            limit: Maximum number of executions to return
            skip: Number of executions to skip (pagination)
            status_filter: Optional status filter
            user: Current user (for access control)

        Returns:
            List of execution dictionaries

        Raises:
            NotFoundError: If workflow not found
            AuthorizationError: If access denied

        """
        # Check workflow exists
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            msg = f"Workflow {workflow_id} not found"
            raise NotFoundError("Workflow", workflow_id)

        # Check access - user may be a dict (TypedDict) or an object
        principal = _normalize_user(user)
        user_id = principal.id if principal else None
        is_admin = principal.is_admin if principal else False
        workflow_owner = workflow.get("user_id")
        if (
            user
            and user_id is not None
            and user_id > 0
            and not is_admin
            and not workflow.get("is_system")
            and workflow_owner is not None
            and workflow_owner != user_id
        ):
            msg = "Access denied to this workflow"
            raise AuthorizationError(msg)

        # Get executions from engine's repository
        all_executions = self.execution_repo.get_workflow_executions(workflow_id, limit + skip)

        # Apply status filter if provided
        if status_filter:
            all_executions = [
                execution
                for execution in all_executions
                if execution.get("status") == status_filter
            ]

        # Apply pagination
        return all_executions[skip : skip + limit]

    def get_execution(
        self,
        workflow_id: str,
        execution_id: str,
        user: Any | None = None,
    ) -> dict[str, Any]:
        """Get single execution with full details.

        Args:
            workflow_id: Workflow ID
            execution_id: Execution ID
            user: Current user (for access control)

        Returns:
            Execution details including step executions

        Raises:
            NotFoundError: If workflow or execution not found
            AuthorizationError: If access denied

        """
        # Check workflow exists
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            msg = f"Workflow {workflow_id} not found"
            raise NotFoundError("Workflow", workflow_id)

        # Check access - user may be a dict (TypedDict) or an object
        principal = _normalize_user(user)
        user_id = principal.id if principal else None
        is_admin = principal.is_admin if principal else False
        workflow_owner = workflow.get("user_id")
        if (
            user
            and user_id is not None
            and user_id > 0
            and not is_admin
            and not workflow.get("is_system")
            and workflow_owner is not None
            and workflow_owner != user_id
        ):
            msg = "Access denied to this workflow"
            raise AuthorizationError(msg)

        # Get execution
        execution = self.execution_repo.get_execution(execution_id)
        if not execution:
            msg = f"Execution {execution_id} not found"
            raise NotFoundError("WorkflowExecution", execution_id)

        # Verify execution belongs to this workflow
        if execution.get("workflow_id") != workflow_id:
            msg = f"Execution {execution_id} not found for workflow {workflow_id}"
            raise NotFoundError("WorkflowExecution", execution_id)

        # Get step executions
        step_executions = self.execution_repo.get_step_executions(execution_id)
        execution["step_executions"] = step_executions if step_executions is not None else []

        return execution

    async def cancel_execution(
        self,
        workflow_id: str,
        execution_id: str,
        user: Any | None = None,
    ) -> dict[str, Any]:
        """Cancel a running execution.

        Args:
            workflow_id: Workflow ID
            execution_id: Execution ID to cancel
            user: Current user (for access control)

        Returns:
            Cancellation result

        Raises:
            NotFoundError: If workflow or execution not found
            AuthorizationError: If access denied
            ValidationError: If execution already completed

        """
        # Check workflow exists
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            msg = f"Workflow {workflow_id} not found"
            raise NotFoundError("Workflow", workflow_id)

        # Check access - user may be a dict (TypedDict) or an object
        principal = _normalize_user(user)
        user_id = principal.id if principal else None
        is_admin = principal.is_admin if principal else False
        workflow_owner = workflow.get("user_id")
        if (
            user
            and user_id is not None
            and user_id > 0
            and not is_admin
            and not workflow.get("is_system")
            and workflow_owner is not None
            and workflow_owner != user_id
        ):
            msg = "Access denied to this workflow"
            raise AuthorizationError(msg)

        # Get execution
        execution = self.execution_repo.get_execution(execution_id)
        if not execution:
            msg = f"Execution {execution_id} not found"
            raise NotFoundError("WorkflowExecution", execution_id)

        # Verify execution belongs to this workflow
        if execution.get("workflow_id") != workflow_id:
            msg = f"Execution {execution_id} not found for workflow {workflow_id}"
            raise NotFoundError("WorkflowExecution", execution_id)

        # Check if already completed
        exec_status = execution.get("status")
        if exec_status in ["completed", "failed", "cancelled"]:
            msg = f"Cannot cancel execution with status: {exec_status}"
            raise ValidationError(msg)

        # Cancel via operations service (queued task removed, running
        # task gets a cancel flag the worker honours between batches).
        # Always reflect the cancellation on the execution row regardless
        # of whether the queue task was still live — the operator clicked
        # cancel, the row should show cancelled.
        try:
            await self.operations_service.abort_operation(execution_id)
        except Exception:
            logger.exception(
                "workflow_cancel_queue_abort_failed",
                execution_id=execution_id,
                workflow_id=workflow_id,
            )
        self.execution_repo.update_status(execution_id, "cancelled")

        return {
            "success": True,
            "execution_id": execution_id,
            "status": "cancelled",
            "message": "Execution cancelled successfully",
        }

    def get_stats(
        self,
        workflow_id: str,
        user: Any | None = None,
    ) -> dict[str, Any]:
        """Get workflow execution stats.

        Args:
            workflow_id: Workflow ID
            user: Current user (for access control)

        Returns:
            Stats dictionary with success/failure rates, avg execution time, etc.

        Raises:
            NotFoundError: If workflow not found
            AuthorizationError: If access denied

        """
        # Check workflow exists
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Check access - user may be a dict (TypedDict) or an object
        principal = _normalize_user(user)
        user_id = principal.id if principal else None
        is_admin = principal.is_admin if principal else False
        workflow_owner = workflow.get("user_id")
        if (
            user
            and user_id is not None
            and user_id > 0
            and not is_admin
            and not workflow.get("is_system")
            and workflow_owner is not None
            and workflow_owner != user_id
        ):
            msg = "Access denied to this workflow"
            raise AuthorizationError(msg)

        # Get all executions for this workflow (use configurable limit for stats)
        executions = self.execution_repo.get_workflow_executions(
            workflow_id, limit=self._stats_max_executions
        )

        # Calculate statistics
        total = len(executions)
        completed = sum(1 for e in executions if e.get("status") == "completed")
        failed = sum(1 for e in executions if e.get("status") == "failed")
        cancelled = sum(1 for e in executions if e.get("status") == "cancelled")
        running = sum(1 for e in executions if e.get("status") == "running")

        # Calculate duration stats (only for completed executions with duration)
        completed_executions = [
            e for e in executions if e.get("status") == "completed" and e.get("duration_ms")
        ]
        avg_duration_ms = 0
        min_duration_ms = None
        max_duration_ms = None
        if completed_executions:
            durations = [e.get("duration_ms", 0) for e in completed_executions]
            avg_duration_ms = sum(durations) / len(durations)
            min_duration_ms = min(durations)
            max_duration_ms = max(durations)

        # Success rate
        success_rate = (completed / total * 100) if total > 0 else 0.0

        # Get last execution times (executions are sorted by created_at desc)
        last_execution_at = None
        last_success_at = None
        last_failure_at = None
        if executions:
            last_execution_at = executions[0].get("created_at")
            # Find last success
            for e in executions:
                if e.get("status") == "completed":
                    last_success_at = e.get("created_at")
                    break
            # Find last failure
            for e in executions:
                if e.get("status") == "failed":
                    last_failure_at = e.get("created_at")
                    break

        # Get current timestamp for updated_at
        from datetime import UTC, datetime

        updated_at = datetime.now(UTC).isoformat()

        return {
            "workflow_id": workflow_id,
            "workflow_name": workflow.get("name"),
            "total_executions": total,
            "successful_executions": completed,
            "failed_executions": failed,
            "cancelled_executions": cancelled,
            "running_executions": running,
            "success_rate": round(success_rate, 2),
            "avg_duration_ms": round(avg_duration_ms),
            "min_duration_ms": min_duration_ms,
            "max_duration_ms": max_duration_ms,
            "last_execution_at": last_execution_at,
            "last_success_at": last_success_at,
            "last_failure_at": last_failure_at,
            "updated_at": updated_at,
        }
