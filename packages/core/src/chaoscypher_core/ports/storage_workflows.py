# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""WorkflowStorageProtocol — storage contract for workflow definitions.

Split from the legacy ``ports/storage.py`` god file on 2026-04-23.
Implemented by ``chaoscypher_core.adapters.sqlite.mixins.workflows.WorkflowsMixin``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import WorkflowDict, WorkflowStepDict


@runtime_checkable
class WorkflowStorageProtocol(Protocol):
    """Storage protocol for workflow operations.

    Handles CRUD for:
    - Workflow definitions
    - Workflow steps
    - Workflow statistics
    - Workflow executions
    """

    # Workflow CRUD
    def get_workflow(self, workflow_id: str) -> WorkflowDict | None:
        """Get workflow by ID. Returns None if not found."""
        ...

    def create_workflow(self, workflow: dict[str, Any]) -> WorkflowDict:
        """Create new workflow. Returns created workflow with generated ID."""
        ...

    def update_workflow(self, workflow_id: str, updates: dict[str, Any]) -> WorkflowDict:
        """Update workflow. Returns updated workflow."""
        ...

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow. Returns True if deleted, False if not found."""
        ...

    def list_workflows(
        self,
        database_name: str,
        category: str | None = None,
        is_system: bool | None = None,
        is_active: bool | None = None,
        expose_as_ai_tool: bool | None = None,
    ) -> list[WorkflowDict]:
        """List workflows with optional filters."""
        ...

    def list_workflows_by_ids(self, ids: list[str]) -> list[WorkflowDict]:
        """Batch-fetch workflows by ID.

        Single SELECT ... WHERE id IN (...). Use to avoid N+1 patterns.

        Returns:
            List of workflow dicts in the same shape as get_workflow();
            order is not guaranteed to match the input ID order. Missing
            IDs are silently omitted from the result. Returns [] for
            an empty input list.
        """
        ...

    # Workflow Steps
    def get_workflow_steps(self, workflow_id: str) -> list[WorkflowStepDict]:
        """Get all steps for a workflow, ordered by step_number."""
        ...

    def get_workflow_step(self, step_id: str) -> WorkflowStepDict | None:
        """Get workflow step by ID."""
        ...

    def create_workflow_step(self, step: dict[str, Any]) -> WorkflowStepDict:
        """Create new workflow step."""
        ...

    def update_workflow_step(self, step_id: str, updates: dict[str, Any]) -> WorkflowStepDict:
        """Update workflow step."""
        ...

    def delete_workflow_step(self, step_id: str) -> bool:
        """Delete workflow step."""
        ...

    def delete_workflow_steps(self, workflow_id: str) -> int:
        """Delete all steps for a workflow. Returns count of deleted steps."""
        ...

    # Workflow Statistics
    def get_workflow_statistics(self, workflow_id: str) -> dict[str, Any] | None:
        """Get statistics for a workflow."""
        ...

    def create_workflow_statistics(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Create workflow statistics."""
        ...

    def update_workflow_statistics(
        self, workflow_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update workflow statistics."""
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 8).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_workflows(self, *, database_name: str) -> int:
        """Count Workflow rows in one database."""
        ...

    def delete_all_workflows(self, *, database_name: str) -> int:
        """Delete every Workflow row in one database. Returns count."""
        ...

    def clear_all_workflow_steps(self) -> int:
        """Delete every WorkflowStep row across databases. Returns count."""
        ...

    def clear_all_workflow_statistics(self) -> int:
        """Delete every WorkflowStatistics row across databases. Returns count."""
        ...

    # ------------------------------------------------------------------
    # Safe-create wrappers (PR2a Task 15).
    # Used by services/workflows/management/io.py once it drops the
    # direct sqlalchemy.exc.IntegrityError import in PR2c Task 29.
    # ------------------------------------------------------------------

    def create_workflow_safe(self, *, workflow: dict[str, Any]) -> dict[str, Any]:
        """Create a Workflow row, raising ConflictError on duplicate name.

        Semantics: ``INSERT``; if the unique constraint on
        ``(database_name, name)`` trips, catch the SQLAlchemy
        ``IntegrityError`` and raise
        ``chaoscypher_core.exceptions.ConflictError`` with the offending
        name in ``details``. Keeps ``sqlalchemy.exc`` out of the service
        layer.

        Args:
            workflow: Dict with id, database_name, name, and any other
                persistable Workflow columns.

        Returns:
            Dict form of the created row (per the dict-over-entity
            contract).

        Raises:
            ConflictError: Duplicate workflow name in the database.
        """
        ...
