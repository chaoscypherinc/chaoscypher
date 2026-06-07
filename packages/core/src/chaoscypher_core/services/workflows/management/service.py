# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Service - Business logic layer for workflow management.

Uses WorkflowStorageProtocol for data access.

SRP REFACTORED: Export/import logic delegated to WorkflowPortabilityService.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.workflows.management.io import WorkflowPortabilityService
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_workflows import WorkflowStorageProtocol
    from chaoscypher_core.ports.types import WorkflowDict, WorkflowStepDict

logger = structlog.get_logger(__name__)


class WorkflowService:
    """Service for managing workflows and workflow steps.

    SRP: Focused on CRUD operations and statistics.
    Delegates export/import to WorkflowPortabilityService.

    Example:
        >>> from chaoscypher_core.services.workflows.api import get_workflow_service
        >>> from chaoscypher_core.adapters.sqlite import get_db_session
        >>> from chaoscypher_core.settings import EngineSettings
        >>>
        >>> # Get service instance via factory
        >>> settings = EngineSettings()
        >>> with get_db_session("my_database") as session:
        ...     service = get_workflow_service(session, settings)
        ...
        ...     # Create a new workflow
        ...     workflow_id = service.create_workflow({
        ...         "name": "Research Pipeline",
        ...         "description": "Automated research workflow",
        ...         "category": "research",
        ...         "input_schema": {"type": "object"},
        ...         "expose_as_ai_tool": True
        ...     })
        ...     print(workflow_id)
        ...     "wf_abc123"
        ...
        ...     # List workflows by category
        ...     workflows = service.list_workflows(category="research", is_active=True)
        ...     print(len(workflows))
        ...     1
        ...
        ...     # Get workflow details
        ...     workflow = service.get_workflow(workflow_id)
        ...     print(workflow["name"])
        ...     "Research Pipeline"

    """

    def __init__(
        self, storage: WorkflowStorageProtocol, database_name: str, tool_service: Any = None
    ) -> None:
        """Initialize workflow service.

        Args:
            storage: WorkflowStorageProtocol implementation for data access
            database_name: Current database name
            tool_service: Optional ToolService instance for tool validation during import

        """
        self.storage = storage
        self.database_name = database_name
        self.tool_service = tool_service

        # SRP: Delegate export/import to specialized service
        self.portability_service = WorkflowPortabilityService(
            repository=storage, tool_service=tool_service
        )

    # ========================================================================
    # Workflow CRUD
    # ========================================================================

    def list_workflows(
        self,
        category: str | None = None,
        is_system: bool | None = None,
        is_active: bool | None = None,
        expose_as_ai_tool: bool | None = None,
    ) -> list[WorkflowDict]:
        """List workflows with optional filters.

        Args:
            category: Filter by category
            is_system: Filter by system flag
            is_active: Filter by active flag
            expose_as_ai_tool: Filter by AI tool exposure

        Returns:
            List of workflow dictionaries

        """
        return self.storage.list_workflows(
            database_name=self.database_name,
            category=category,
            is_system=is_system,
            is_active=is_active,
            expose_as_ai_tool=expose_as_ai_tool,
        )
        # Storage layer already returns dicts - no conversion needed

    def get_workflow(self, workflow_id: str) -> WorkflowDict | None:
        """Get workflow by ID.

        Args:
            workflow_id: Workflow ID

        Returns:
            Workflow dictionary or None

        """
        # Storage layer already returns dict - no conversion needed
        return self.storage.get_workflow(workflow_id)

    def list_workflows_by_ids(self, ids: list[str]) -> list[WorkflowDict]:
        """Batch-fetch workflows by ID.

        Single SELECT ... WHERE id IN (...). Use instead of repeated
        get_workflow() calls to avoid O(N) DB round-trips.

        Args:
            ids: Workflow IDs to fetch.

        Returns:
            List of workflow dicts in the same shape as get_workflow();
            order is not guaranteed to match the input ID order. Missing
            IDs are silently omitted from the result.

        """
        return self.storage.list_workflows_by_ids(ids)

    def create_workflow(self, workflow_data: dict[str, Any]) -> str:
        """Create a new workflow.

        Args:
            workflow_data: Workflow data dictionary

        Returns:
            Created workflow ID

        """
        # Build workflow data dict (storage protocol expects dict, returns dict)
        workflow_id = workflow_data.get("id", generate_id())
        now = datetime.now(UTC)
        workflow_dict = {
            "id": workflow_id,
            "database_name": self.database_name,
            "name": workflow_data["name"],
            "description": workflow_data.get("description"),
            "category": workflow_data.get("category"),
            "is_system": workflow_data.get("is_system", False),
            "is_active": workflow_data.get("is_active", True),
            "expose_as_ai_tool": workflow_data.get("expose_as_ai_tool", False),
            "input_schema": workflow_data["input_schema"],
            "output_schema": workflow_data.get("output_schema"),
            "allow_parallel_execution": workflow_data.get("allow_parallel_execution", True),
            "timeout_seconds": workflow_data.get("timeout_seconds"),
            "max_retries": workflow_data.get("max_retries", 0),
            "tags": workflow_data.get("tags", []),
            "icon": workflow_data.get("icon"),
            "version": workflow_data.get("version", "1.0.0"),
            "created_by": workflow_data.get("created_by", "user"),
            "created_at": now,
            "updated_at": now,
        }

        # Create via storage (returns dict)
        created = self.storage.create_workflow(workflow_dict)

        # Create statistics entry
        stats_dict = {
            "workflow_id": created["id"],
            "updated_at": now,
        }
        self.storage.create_workflow_statistics(stats_dict)

        logger.info(
            "workflow_created",
            workflow_id=created["id"],
            workflow_name=created["name"],
            category=created.get("category"),
        )
        return created["id"]

    def update_workflow(self, workflow_id: str, updates: dict[str, Any]) -> bool:
        """Update workflow.

        Args:
            workflow_id: Workflow ID
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if not found

        """
        workflow = self.storage.get_workflow(workflow_id)
        if not workflow:
            return False

        # Build update dict with ONLY the fields being modified.
        # Avoid copying the full workflow - fields like created_at come back
        # from storage as ISO strings which would break SQLAlchemy DateTime.
        allowed_fields = [
            "name",
            "description",
            "category",
            "is_active",
            "expose_as_ai_tool",
            "input_schema",
            "output_schema",
            "allow_parallel_execution",
            "timeout_seconds",
            "max_retries",
            "tags",
            "icon",
        ]
        workflow_update: dict[str, Any] = {
            field: updates[field] for field in allowed_fields if field in updates
        }

        workflow_update["updated_at"] = datetime.now(UTC)
        self.storage.update_workflow(workflow["id"], workflow_update)
        logger.info(
            "workflow_updated",
            workflow_id=workflow_id,
            workflow_name=workflow.get("name"),
            updated_fields=list(updates.keys()),
        )
        return True

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            True if deleted, False if not found

        """
        workflow = self.storage.get_workflow(workflow_id)
        if not workflow:
            return False

        workflow_name = workflow.get("name")
        self.storage.delete_workflow(workflow["id"])
        logger.info("workflow_deleted", workflow_id=workflow_id, workflow_name=workflow_name)
        return True

    # ========================================================================
    # Workflow Steps CRUD
    # ========================================================================

    def list_workflow_steps(self, workflow_id: str) -> list[WorkflowStepDict]:
        """List all steps for a workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            List of step dictionaries

        """
        # Storage layer already returns dicts - no conversion needed
        return self.storage.get_workflow_steps(workflow_id)

    def get_workflow_step(self, step_id: str) -> WorkflowStepDict | None:
        """Get workflow step by ID.

        Args:
            step_id: Step ID

        Returns:
            Step dictionary or None

        """
        # Storage layer already returns dict - no conversion needed
        return self.storage.get_workflow_step(step_id)

    def create_workflow_step(self, step_data: dict[str, Any]) -> str:
        """Create a new workflow step.

        Args:
            step_data: Step data dictionary

        Returns:
            Created step ID

        """
        # Build step data dict (storage protocol expects dict, returns dict)
        now = datetime.now(UTC)
        step_dict = {
            "id": step_data.get("id", f"step_{now.timestamp()}"),
            "workflow_id": step_data["workflow_id"],
            "step_number": step_data["step_number"],
            "name": step_data["name"],
            "description": step_data.get("description"),
            "tool_type": step_data["tool_type"],
            "tool_id": step_data["tool_id"],
            "configuration": step_data["configuration"],
            "condition": step_data.get("condition"),
            "retry_on_failure": step_data.get("retry_on_failure", False),
            "timeout_seconds": step_data.get("timeout_seconds"),
            "depends_on": step_data.get("depends_on", []),
            "continue_on_error": step_data.get("continue_on_error", False),
            "thinking_mode": step_data.get("thinking_mode"),
            "created_at": now,
            "updated_at": now,
        }

        created = self.storage.create_workflow_step(step_dict)
        logger.info(
            "workflow_step_created",
            step_id=created["id"],
            step_name=created["name"],
            workflow_id=created["workflow_id"],
            step_number=created["step_number"],
            tool_type=created["tool_type"],
            tool_id=created["tool_id"],
        )

        return created["id"]

    def update_workflow_step(self, step_id: str, updates: dict[str, Any]) -> bool:
        """Update workflow step.

        Args:
            step_id: Step ID
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if not found

        """
        step = self.storage.get_workflow_step(step_id)
        if not step:
            return False

        # Build updates dict with allowed fields (dict mutation, not setattr)
        allowed_fields = [
            "step_number",
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
        ]
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        filtered_updates["updated_at"] = datetime.now(UTC)

        self.storage.update_workflow_step(step_id, filtered_updates)
        logger.info(
            "workflow_step_updated",
            step_id=step_id,
            step_name=step.get("name"),
            workflow_id=step.get("workflow_id"),
            updated_fields=list(updates.keys()),
        )
        return True

    def delete_workflow_step(self, step_id: str) -> bool:
        """Delete workflow step.

        Args:
            step_id: Step ID

        Returns:
            True if deleted, False if not found

        """
        step = self.storage.get_workflow_step(step_id)
        if not step:
            return False

        step_name = step.get("name")
        workflow_id = step.get("workflow_id")
        self.storage.delete_workflow_step(step_id)
        logger.info(
            "workflow_step_deleted", step_id=step_id, step_name=step_name, workflow_id=workflow_id
        )
        return True

    def delete_workflow_steps(self, workflow_id: str) -> int:
        """Delete all steps for a workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            Number of steps deleted

        """
        count = self.storage.delete_workflow_steps(workflow_id)
        logger.info("workflow_steps_deleted", workflow_id=workflow_id, deleted_count=count)
        return count

    # ========================================================================
    # Workflow Statistics
    # ========================================================================

    def get_workflow_statistics(self, workflow_id: str) -> dict[str, Any] | None:
        """Get workflow execution statistics.

        Args:
            workflow_id: Workflow ID

        Returns:
            Statistics dictionary or None

        """
        # Storage layer already returns dict - no conversion needed
        return self.storage.get_workflow_statistics(workflow_id)

    def update_workflow_statistics(
        self,
        workflow_id: str,
        duration_ms: int,
        status: str,
    ) -> None:
        """Update workflow statistics after execution.

        Args:
            workflow_id: Workflow ID
            duration_ms: Execution duration in milliseconds
            status: Execution status ('success', 'failed', 'cancelled')

        """
        now = datetime.now(UTC)
        stats = self.storage.get_workflow_statistics(workflow_id)

        if not stats:
            # Create new stats dict
            stats_dict = {
                "workflow_id": workflow_id,
                "total_executions": 0,
                "successful_executions": 0,
                "failed_executions": 0,
                "cancelled_executions": 0,
                "avg_duration_ms": 0,
                "min_duration_ms": None,
                "max_duration_ms": None,
                "last_execution_at": None,
                "last_success_at": None,
                "last_failure_at": None,
                "updated_at": now,
            }
            stats = self.storage.create_workflow_statistics(stats_dict)

        # Build updates dict (storage returns dict, so use dict access)
        total_executions = stats.get("total_executions", 0) + 1
        successful = stats.get("successful_executions", 0)
        failed = stats.get("failed_executions", 0)
        cancelled = stats.get("cancelled_executions", 0)
        min_dur = stats.get("min_duration_ms")
        max_dur = stats.get("max_duration_ms")
        avg_dur = stats.get("avg_duration_ms", 0)

        updates: dict[str, Any] = {
            "total_executions": total_executions,
            "last_execution_at": now,
            "updated_at": now,
        }

        if status == "success":
            updates["successful_executions"] = successful + 1
            updates["last_success_at"] = now
        elif status == "failed":
            updates["failed_executions"] = failed + 1
            updates["last_failure_at"] = now
        elif status == "cancelled":
            updates["cancelled_executions"] = cancelled + 1

        # Update duration stats
        if min_dur is None or duration_ms < min_dur:
            updates["min_duration_ms"] = duration_ms
        if max_dur is None or duration_ms > max_dur:
            updates["max_duration_ms"] = duration_ms

        # Calculate average duration
        total_duration = avg_dur * (total_executions - 1) + duration_ms
        updates["avg_duration_ms"] = int(total_duration / total_executions)

        self.storage.update_workflow_statistics(workflow_id, updates)

    # ========================================================================
    # Workflow Export/Import (Delegated to WorkflowPortabilityService)
    # ========================================================================

    def export_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Export a workflow with all its steps to portable JSON format.

        SRP: Delegates to WorkflowPortabilityService.

        Args:
            workflow_id: Workflow ID to export

        Returns:
            Dict in WorkflowExportFormat structure

        Raises:
            ValueError: If workflow not found

        """
        return self.portability_service.export_workflow(workflow_id)

    def import_workflow(
        self,
        workflow_data: dict[str, Any],
        on_duplicate: str = "fail",
        new_name: str | None = None,
        import_as_inactive: bool = False,
    ) -> dict[str, Any]:
        """Import a workflow from exported JSON format.

        SRP: Delegates to WorkflowPortabilityService.

        Args:
            workflow_data: Exported workflow data (WorkflowExportFormat structure)
            on_duplicate: How to handle duplicate names: "fail" | "skip" | "rename"
            new_name: Optional override for workflow name
            import_as_inactive: Import with is_active=False

        Returns:
            Dict with 'workflow_id' (str), 'message' (str), 'was_existing' (bool)

        Raises:
            ValueError: If validation fails or duplicate handling fails

        """
        return self.portability_service.import_workflow(
            workflow_data=workflow_data,
            on_duplicate=on_duplicate,
            new_name=new_name,
            import_as_inactive=import_as_inactive,
        )

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def get_global_stats(self) -> dict[str, Any]:
        """Get aggregated stats across all workflows.

        Returns:
            Dictionary with global workflow stats

        """
        # Get all workflows
        workflows = self.list_workflows()

        # Initialize aggregated stats
        total_workflows = len(workflows)
        active_workflows = sum(1 for w in workflows if w.get("is_active", False))
        total_executions = 0
        successful_executions = 0
        failed_executions = 0
        cancelled_executions = 0

        # Aggregate from individual workflow statistics
        for workflow_dict in workflows:
            workflow_id = workflow_dict["id"]
            stats = self.storage.get_workflow_statistics(workflow_id)
            if stats:
                # Storage layer returns dict - use dict access
                total_executions += stats.get("total_executions", 0)
                successful_executions += stats.get("successful_executions", 0)
                failed_executions += stats.get("failed_executions", 0)
                cancelled_executions += stats.get("cancelled_executions", 0)

        # Calculate success rate
        success_rate = (
            (successful_executions / total_executions * 100) if total_executions > 0 else 0.0
        )

        return {
            "total_workflows": total_workflows,
            "active_workflows": active_workflows,
            "inactive_workflows": total_workflows - active_workflows,
            "total_executions": total_executions,
            "successful_executions": successful_executions,
            "failed_executions": failed_executions,
            "cancelled_executions": cancelled_executions,
            "success_rate": round(success_rate, 2),
        }
