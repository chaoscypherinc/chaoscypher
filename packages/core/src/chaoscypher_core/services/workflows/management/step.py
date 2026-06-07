# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Steps Service.

Business logic for workflow step management.
Uses WorkflowStorageProtocol for data access - all data is dict-based.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_workflows import WorkflowStorageProtocol
    from chaoscypher_core.ports.types import WorkflowStepDict


class WorkflowStepsService:
    """Service for workflow step operations.

    All storage protocol methods return dicts - use dict access patterns only.
    """

    def __init__(self, repository: WorkflowStorageProtocol):
        """Initialize workflow steps service.

        Args:
            repository: WorkflowStorageProtocol instance

        """
        self.repository = repository

    def list_steps(self, workflow_id: str, user: Any | None = None) -> list[WorkflowStepDict]:
        """List all steps for a workflow.

        Args:
            workflow_id: Workflow ID
            user: Optional user for auth

        Returns:
            List of step dictionaries sorted by step_number

        Raises:
            NotFoundError: If workflow not found
            AuthorizationError: If access denied

        """
        # Get workflow to verify it exists and check permissions
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Get steps via protocol method (returns dicts sorted by step_number)
        return self.repository.get_workflow_steps(workflow_id)

    def get_step(self, workflow_id: str, step_id: str, user: Any | None = None) -> WorkflowStepDict:
        """Get a specific workflow step.

        Args:
            workflow_id: Workflow ID
            step_id: Step ID
            user: Optional user for auth

        Returns:
            Step dictionary

        Raises:
            NotFoundError: If workflow or step not found
            AuthorizationError: If access denied

        """
        # Get workflow to verify it exists
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Get step via protocol method (returns dict)
        step = self.repository.get_workflow_step(step_id)
        if not step or step.get("workflow_id") != workflow_id:
            raise NotFoundError("WorkflowStep", step_id)

        return step

    def create_step(
        self, workflow_id: str, step_data: dict[str, Any], user: Any | None = None
    ) -> WorkflowStepDict:
        """Create a new workflow step.

        Args:
            workflow_id: Workflow ID
            step_data: Step data dictionary
            user: Optional user for auth

        Returns:
            Created step dictionary

        Raises:
            NotFoundError: If workflow not found
            AuthorizationError: If system workflow

        """
        # Get workflow to verify it exists (returns dict)
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Check if workflow is system workflow (cannot modify)
        if workflow.get("is_system"):
            msg = "Cannot modify system workflows"
            raise AuthorizationError(msg)

        # Generate step ID if not provided
        now = datetime.now(UTC)
        step_id = step_data.get("id", f"step_{now.timestamp()}")

        # Determine step number (auto-increment if not provided)
        step_number = step_data.get("step_number")
        if step_number is None:
            # Get existing steps and find max step_number
            existing_steps = self.repository.get_workflow_steps(workflow_id)
            max_step = max((s.get("step_number", 0) for s in existing_steps), default=0)
            step_number = max_step + 1

        # Build step dict (storage protocol expects dict, returns dict)
        step_dict = {
            "id": step_id,
            "workflow_id": workflow_id,
            "step_number": step_number,
            "name": step_data["name"],
            "description": step_data.get("description"),
            "tool_type": step_data["tool_type"],
            "tool_id": step_data["tool_id"],
            "configuration": step_data.get("configuration", {}),
            "condition": step_data.get("condition"),
            "retry_on_failure": step_data.get("retry_on_failure", False),
            "timeout_seconds": step_data.get("timeout_seconds"),
            "depends_on": step_data.get("depends_on", []),
            "continue_on_error": step_data.get("continue_on_error", False),
            "thinking_mode": step_data.get("thinking_mode"),
            "created_at": now,
            "updated_at": now,
        }

        # Create step via protocol method (returns dict)
        created_step = self.repository.create_workflow_step(step_dict)

        # Update workflow's updated_at timestamp
        self.repository.update_workflow(workflow_id, {"updated_at": now})

        return created_step

    def update_step(
        self, workflow_id: str, step_id: str, step_data: dict[str, Any], user: Any | None = None
    ) -> WorkflowStepDict:
        """Update an existing workflow step.

        Args:
            workflow_id: Workflow ID
            step_id: Step ID
            step_data: Partial step data to update
            user: Optional user for auth

        Returns:
            Updated step dictionary

        Raises:
            NotFoundError: If workflow or step not found
            AuthorizationError: If system workflow

        """
        # Get workflow to verify it exists (returns dict)
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Check if workflow is system workflow (cannot modify)
        if workflow.get("is_system"):
            msg = "Cannot modify system workflows"
            raise AuthorizationError(msg)

        # Get step from repository (returns dict)
        step = self.repository.get_workflow_step(step_id)
        if not step or step.get("workflow_id") != workflow_id:
            raise NotFoundError("WorkflowStep", step_id)

        # Build updates dict with allowed fields
        now = datetime.now(UTC)
        allowed_fields = [
            "name",
            "description",
            "tool_type",
            "tool_id",
            "configuration",
            "condition",
            "retry_on_failure",
            "timeout_seconds",
            "depends_on",
            "continue_on_error",
            "thinking_mode",
            "step_number",
        ]
        updates = {k: v for k, v in step_data.items() if k in allowed_fields}
        updates["updated_at"] = now

        # Update via protocol method (returns dict)
        updated_step = self.repository.update_workflow_step(step_id, updates)

        # Update workflow's updated_at timestamp
        self.repository.update_workflow(workflow_id, {"updated_at": now})

        return updated_step

    def delete_step(self, workflow_id: str, step_id: str, user: Any | None = None) -> None:
        """Delete a workflow step.

        Args:
            workflow_id: Workflow ID
            step_id: Step ID
            user: Optional user for auth

        Raises:
            NotFoundError: If workflow or step not found
            AuthorizationError: If system workflow

        """
        # Get workflow to verify it exists (returns dict)
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Check if workflow is system workflow (cannot modify)
        if workflow.get("is_system"):
            msg = "Cannot modify system workflows"
            raise AuthorizationError(msg)

        # Get step from repository (returns dict)
        step = self.repository.get_workflow_step(step_id)
        if not step or step.get("workflow_id") != workflow_id:
            raise NotFoundError("WorkflowStep", step_id)

        # Delete step via protocol method
        self.repository.delete_workflow_step(step_id)

        # Update workflow's updated_at timestamp
        now = datetime.now(UTC)
        self.repository.update_workflow(workflow_id, {"updated_at": now})

    def reorder_steps(
        self, workflow_id: str, step_order: list[str], user: Any | None = None
    ) -> list[WorkflowStepDict]:
        """Reorder workflow steps.

        Args:
            workflow_id: Workflow ID
            step_order: List of step IDs in desired order
            user: Optional user for auth

        Returns:
            List of reordered steps

        Raises:
            NotFoundError: If workflow not found
            AuthorizationError: If system workflow
            ValidationError: If invalid step order

        """
        # Get workflow to verify it exists (returns dict)
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Check if workflow is system workflow (cannot modify)
        if workflow.get("is_system"):
            msg = "Cannot modify system workflows"
            raise AuthorizationError(msg)

        # Get existing steps (returns list of dicts)
        existing_steps = self.repository.get_workflow_steps(workflow_id)

        # Validate all step IDs exist in workflow
        workflow_step_ids = {s["id"] for s in existing_steps}
        order_step_ids = set(step_order)

        if order_step_ids != workflow_step_ids:
            missing = workflow_step_ids - order_step_ids
            extra = order_step_ids - workflow_step_ids
            error_parts = []
            if missing:
                error_parts.append(f"Missing step IDs: {', '.join(missing)}")
            if extra:
                error_parts.append(f"Unknown step IDs: {', '.join(extra)}")

            msg = f"Invalid step order: {'; '.join(error_parts)}"
            raise ValidationError(msg)

        # Update step numbers
        now = datetime.now(UTC)
        updated_steps = []
        for idx, step_id in enumerate(step_order, start=1):
            updates = {"step_number": idx, "updated_at": now}
            updated_step = self.repository.update_workflow_step(step_id, updates)
            updated_steps.append(updated_step)

        # Update workflow's updated_at timestamp
        self.repository.update_workflow(workflow_id, {"updated_at": now})

        return updated_steps
