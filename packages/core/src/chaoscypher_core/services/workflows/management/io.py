# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Portability Service.

Handles workflow export and import operations for portable JSON format.
Separated from WorkflowService to follow Single Responsibility Principle.
Uses WorkflowStorageProtocol for data access - all data is dict-based.

Responsibilities:
- Export workflows to portable JSON format
- Import workflows from portable JSON format
- Validate import data
- Handle name conflicts (fail/skip/rename)
- Validate tool references
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.exceptions import (
    ConflictError,
    NotFoundError,
    OperationError,
    ValidationError,
)
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_workflows import WorkflowStorageProtocol
    from chaoscypher_core.ports.types import WorkflowDict, WorkflowStepDict

logger = structlog.get_logger(__name__)


class WorkflowPortabilityService:
    """Service for workflow export/import operations.

    Handles portable JSON format for workflows, enabling
    sharing and migration across databases.

    All storage protocol methods return dicts - use dict access patterns only.
    """

    def __init__(
        self,
        repository: WorkflowStorageProtocol,
        tool_service: Any = None,
        database_name: str = "",
    ) -> None:
        """Initialize portability service.

        Args:
            repository: WorkflowStorageProtocol for database operations
            tool_service: Optional ToolService for validating tool references
            database_name: Database name for creating new entities

        """
        self.repository = repository
        self.tool_service = tool_service
        self.database_name = database_name

    # ========================================================================
    # Export Operations
    # ========================================================================

    def export_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Export a workflow with all its steps to portable JSON format.

        Creates a version-stamped export that includes workflow metadata
        and all steps, excluding database-specific IDs.

        Args:
            workflow_id: Workflow ID to export

        Returns:
            Dict in WorkflowExportFormat structure:
            {
                'version': '1.0',
                'exported_at': '2025-11-09T...',
                'workflow': {...},
                'steps': [...]
            }

        Raises:
            NotFoundError: If workflow not found

        """
        # Fetch workflow from repository (returns dict)
        workflow = self.repository.get_workflow(workflow_id)
        if not workflow:
            raise NotFoundError("Workflow", workflow_id)

        # Fetch steps from repository (returns list of dicts)
        steps = self.repository.get_workflow_steps(workflow_id)

        # Serialize workflow (exclude system fields)
        workflow_data = self._serialize_workflow(workflow)

        # Serialize steps (exclude system fields, sorted by step_number)
        steps_data = self._serialize_steps(steps)

        # Return export format
        return {
            "version": "1.0",
            "exported_at": datetime.now(UTC).isoformat() + "Z",
            "workflow": workflow_data,
            "steps": steps_data,
        }

    def _serialize_workflow(self, workflow: WorkflowDict) -> dict[str, Any]:
        """Serialize workflow dict to portable format.

        Excludes database-specific fields (id, database_name, created_at, etc.)

        Args:
            workflow: Workflow dict from storage

        Returns:
            Portable workflow dict

        """
        return {
            "name": workflow.get("name"),
            "description": workflow.get("description"),
            "category": workflow.get("category"),
            "is_system": workflow.get("is_system", False),
            "is_active": workflow.get("is_active", True),
            "expose_as_ai_tool": workflow.get("expose_as_ai_tool", False),
            "input_schema": workflow.get("input_schema"),
            "output_schema": workflow.get("output_schema"),
            "allow_parallel_execution": workflow.get("allow_parallel_execution", True),
            "timeout_seconds": workflow.get("timeout_seconds"),
            "max_retries": workflow.get("max_retries", 0),
            "tags": workflow.get("tags") or [],
            "icon": workflow.get("icon"),
            "version": workflow.get("version", "1.0.0"),
        }

    def _serialize_steps(self, steps: list[WorkflowStepDict]) -> list[dict[str, Any]]:
        """Serialize workflow steps to portable format.

        Excludes database-specific fields (id, workflow_id, created_at, etc.)
        Sorted by step_number for consistent ordering.

        Args:
            steps: List of step dicts from storage

        Returns:
            List of portable step dicts

        """
        steps_data = []
        for step in sorted(steps, key=lambda s: s.get("step_number", 0)):
            step_dict = {
                "step_number": step.get("step_number"),
                "name": step.get("name"),
                "description": step.get("description"),
                "tool_type": step.get("tool_type"),
                "tool_id": step.get("tool_id"),
                "configuration": step.get("configuration", {}),
                "condition": step.get("condition"),
                "retry_on_failure": step.get("retry_on_failure", False),
                "timeout_seconds": step.get("timeout_seconds"),
                "depends_on": step.get("depends_on") or [],
                "continue_on_error": step.get("continue_on_error", False),
                "thinking_mode": step.get("thinking_mode"),
            }
            steps_data.append(step_dict)
        return steps_data

    # ========================================================================
    # Import Operations
    # ========================================================================

    def import_workflow(
        self,
        workflow_data: dict[str, Any],
        on_duplicate: str = "fail",
        new_name: str | None = None,
        import_as_inactive: bool = False,
    ) -> dict[str, Any]:
        """Import a workflow from exported JSON format.

        Performs validation, conflict resolution, and entity creation.

        Args:
            workflow_data: Exported workflow data (WorkflowExportFormat structure)
            on_duplicate: How to handle duplicate names: "fail" | "skip" | "rename"
            new_name: Optional override for workflow name
            import_as_inactive: Import with is_active=False

        Returns:
            Dict with 'workflow_id' (str), 'message' (str), 'was_existing' (bool)

        Raises:
            ValidationError: If validation fails (missing fields, bad version, bad tool refs).
            ConflictError: If on_duplicate="fail" and name already exists.
            OperationError: If name collisions persist after all retry attempts.

        """
        # 1. Validate export format
        self._validate_import_data(workflow_data)

        # 2. Extract and prepare workflow data
        workflow_dict = workflow_data["workflow"]
        initial_name = new_name if new_name else workflow_dict["name"]

        # Apply import_as_inactive option
        is_active = workflow_dict.get("is_active", True)
        if import_as_inactive:
            is_active = False

        # 3. Validate tool references (static, not affected by concurrency)
        steps_data = workflow_data["steps"]
        self._validate_tool_references(steps_data)

        # 4. Retry loop: compute rename within a transaction, retry on collision.
        max_attempts = get_settings().retries.workflow_rename_max_attempts
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            duplicate_result = self._handle_duplicate_name(initial_name, on_duplicate)
            if isinstance(duplicate_result, dict):
                return duplicate_result  # Skipped import (idempotent)
            final_name = duplicate_result if isinstance(duplicate_result, str) else initial_name
            try:
                transaction_cm = getattr(self.repository, "transaction", None)
                if transaction_cm is not None:
                    with transaction_cm():
                        workflow_id = self._create_workflow_entities(
                            workflow_dict=workflow_dict,
                            steps_data=steps_data,
                            final_name=final_name,
                            is_active=is_active,
                        )
                else:
                    workflow_id = self._create_workflow_entities(
                        workflow_dict=workflow_dict,
                        steps_data=steps_data,
                        final_name=final_name,
                        is_active=is_active,
                    )
                logger.info(
                    "workflow_imported",
                    workflow_name=final_name,
                    workflow_id=workflow_id,
                    steps_count=len(steps_data),
                    attempt=attempt + 1,
                )
                return {
                    "workflow_id": workflow_id,
                    "message": (
                        f"Workflow '{final_name}' imported successfully "
                        f"with {len(steps_data)} steps."
                    ),
                    "was_existing": False,
                }
            except ConflictError as exc:
                last_exc = exc
                logger.warning(
                    "workflow_import_name_collision_retry",
                    attempted_name=final_name,
                    attempt=attempt + 1,
                )
                continue

        msg = (
            f"Failed to import workflow '{initial_name}' after {max_attempts} "
            f"attempts due to repeated name collisions."
        )
        raise OperationError(msg, operation="workflow_import") from last_exc

    def _validate_import_data(self, workflow_data: dict[str, Any]) -> None:
        """Validate import data structure and version.

        Raises:
            ValidationError: If a required field is missing or the version is incompatible.

        """
        # Check required top-level fields
        if "version" not in workflow_data:
            msg = "Invalid workflow data: missing 'version' field"
            raise ValidationError(msg, field="version")
        if "workflow" not in workflow_data:
            msg = "Invalid workflow data: missing 'workflow' field"
            raise ValidationError(msg, field="workflow")
        if "steps" not in workflow_data:
            msg = "Invalid workflow data: missing 'steps' field"
            raise ValidationError(msg, field="steps")

        # Check version compatibility
        if workflow_data["version"] != "1.0":
            msg = f"Incompatible workflow version: {workflow_data['version']}. Expected '1.0'"
            raise ValidationError(msg, field="version")

        # Validate required workflow fields
        workflow_dict = workflow_data["workflow"]
        required_fields = ["name", "input_schema", "output_schema"]
        for field in required_fields:
            if field not in workflow_dict:
                msg = f"Invalid workflow data: missing '{field}' field in workflow"
                raise ValidationError(msg, field=field)

    def _handle_duplicate_name(self, name: str, on_duplicate: str) -> str | dict[str, Any] | None:
        """Handle name conflicts based on on_duplicate strategy.

        Args:
            name: Workflow name to check
            on_duplicate: "fail" | "skip" | "rename"

        Returns:
            - None if no conflict
            - Dict with existing workflow info if skipped
            - str with new name if renamed

        Raises:
            ConflictError: If on_duplicate="fail" and name exists.

        """
        # Check for duplicates (storage returns list of dicts)
        existing_workflows = self.repository.list_workflows(
            database_name=self.database_name,
        )
        existing_names = {w.get("name") for w in existing_workflows}

        if name not in existing_names:
            return None  # No conflict

        # Handle conflict
        if on_duplicate == "fail":
            msg = f"Workflow '{name}' already exists. Use on_duplicate='skip' or 'rename'."
            raise ConflictError(msg)

        if on_duplicate == "skip":
            # Find the existing workflow with this name
            existing_workflow = next((w for w in existing_workflows if w.get("name") == name), None)
            if existing_workflow:
                return {
                    "workflow_id": existing_workflow.get("id"),
                    "message": f"Workflow '{name}' already exists, skipped import.",
                    "was_existing": True,
                }

        if on_duplicate == "rename":
            # Append " (imported)" and check again, keep appending numbers if needed
            base_name = name + " (imported)"
            final_name = base_name
            counter = 2
            while final_name in existing_names:
                final_name = f"{base_name} ({counter})"
                counter += 1
            return final_name

        return None

    def _validate_tool_references(self, steps_data: list[dict[str, Any]]) -> None:
        """Validate that all tool references exist.

        Args:
            steps_data: List of step dictionaries

        Raises:
            ValidationError: If a referenced tool doesn't exist.

        """
        if not steps_data or not self.tool_service:
            return

        # Get all available tools
        system_tools = self.tool_service.list_system_tools()
        user_tools = self.tool_service.list_user_tools()

        # Create a set of all tool IDs
        all_tool_ids = {t["id"] for t in system_tools} | {t["id"] for t in user_tools}

        # Check each step's tool_id
        for step in steps_data:
            tool_id = step.get("tool_id")
            if tool_id and tool_id not in all_tool_ids:
                msg = f"Tool not found: {tool_id}. Please install required tools before importing."
                raise ValidationError(msg, field="tool_id")

    def _create_workflow_entities(
        self,
        workflow_dict: dict[str, Any],
        steps_data: list[dict[str, Any]],
        final_name: str,
        is_active: bool,
    ) -> str:
        """Create workflow and step dicts in database via storage protocol.

        Args:
            workflow_dict: Workflow data from import
            steps_data: Steps data from import
            final_name: Final workflow name (after conflict resolution)
            is_active: Active status

        Returns:
            Created workflow ID

        """
        # Create workflow ID
        workflow_id = generate_id()
        now = datetime.now(UTC)

        # Build workflow dict (storage protocol expects dict, returns dict)
        new_workflow = {
            "id": workflow_id,
            "database_name": self.database_name,
            "name": final_name,
            "description": workflow_dict.get("description"),
            "category": workflow_dict.get("category"),
            "is_system": workflow_dict.get("is_system", False),
            "is_active": is_active,
            "expose_as_ai_tool": workflow_dict.get("expose_as_ai_tool", False),
            "input_schema": workflow_dict["input_schema"],
            "output_schema": workflow_dict["output_schema"],
            "allow_parallel_execution": workflow_dict.get("allow_parallel_execution", True),
            "timeout_seconds": workflow_dict.get("timeout_seconds"),
            "max_retries": workflow_dict.get("max_retries", 0),
            "tags": workflow_dict.get("tags", []),
            "icon": workflow_dict.get("icon"),
            "version": workflow_dict.get("version", "1.0.0"),
            "created_by": "user",
            "created_at": now,
            "updated_at": now,
        }

        # Create workflow via the safe variant that wraps IntegrityError
        # as ConflictError (see WorkflowStorageProtocol.create_workflow_safe).
        # The import retry loop above catches ConflictError to rename and
        # retry on duplicate-name collisions.
        created_workflow = self.repository.create_workflow_safe(workflow=new_workflow)

        # Create statistics entry
        stats_dict = {
            "workflow_id": created_workflow["id"],
            "updated_at": now,
        }
        self.repository.create_workflow_statistics(stats_dict)

        # Create steps
        for step_data in steps_data:
            step_id = generate_id()

            step_dict = {
                "id": step_id,
                "workflow_id": workflow_id,
                "step_number": step_data["step_number"],
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

            self.repository.create_workflow_step(step_dict)

        return workflow_id
