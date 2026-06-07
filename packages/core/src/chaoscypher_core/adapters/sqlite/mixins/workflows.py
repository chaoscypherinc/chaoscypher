# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Storage Protocol Mixin for SqliteAdapter."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import (
    Workflow,
    WorkflowStatistics,
    WorkflowStep,
)
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.ports.storage_workflows import WorkflowStorageProtocol


class WorkflowsMixin(SqliteMixinBase, WorkflowStorageProtocol):
    """Mixin implementing WorkflowStorageProtocol for SQLite storage.

    Implements CRUD operations for:
    - Workflows (definitions)
    - Workflow steps
    - Workflow statistics

    Note:
        Workflow execution tracking moved to WorkflowExecutionsMixin per ISP.

    """

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        """Get workflow by ID."""
        self._ensure_connected()
        workflow = self.session.get(Workflow, workflow_id)
        return self._entity_to_dict(workflow) if workflow else None

    def create_workflow(self, workflow_data: dict[str, Any]) -> dict[str, Any]:
        """Create new workflow."""
        self._ensure_connected()
        workflow = Workflow(**workflow_data)
        self.session.add(workflow)
        self._maybe_commit()
        self.session.refresh(workflow)
        return self._entity_to_dict(workflow)

    def update_workflow(self, workflow_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update workflow."""
        self._ensure_connected()
        workflow = self.session.get(Workflow, workflow_id)
        if not workflow:
            msg = "Workflow"
            raise NotFoundError(msg, workflow_id)

        for key, value in updates.items():
            setattr(workflow, key, value)

        workflow.updated_at = datetime.now(UTC)
        self.session.add(workflow)
        self._maybe_commit()
        self.session.refresh(workflow)
        return self._entity_to_dict(workflow)

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow."""
        self._ensure_connected()
        workflow = self.session.get(Workflow, workflow_id)
        if not workflow:
            return False

        self.session.delete(workflow)
        self._maybe_commit()
        return True

    def list_workflows(
        self,
        database_name: str,
        category: str | None = None,
        is_system: bool | None = None,
        is_active: bool | None = None,
        expose_as_ai_tool: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List workflows with optional filters.

        Uses load_only() to prevent eager/lazy loading of relationship
        columns (steps, statistics) which would trigger additional queries.
        """
        self._ensure_connected()
        stmt = (
            select(Workflow)
            .options(
                load_only(
                    Workflow.id,
                    Workflow.database_name,
                    Workflow.name,
                    Workflow.description,
                    Workflow.category,
                    Workflow.is_system,
                    Workflow.is_active,
                    Workflow.expose_as_ai_tool,
                    Workflow.allow_parallel_execution,
                    Workflow.timeout_seconds,
                    Workflow.max_retries,
                    Workflow.tags,
                    Workflow.icon,
                    Workflow.version,
                    Workflow.created_by,
                    Workflow.created_at,
                    Workflow.updated_at,
                    Workflow.last_executed_at,
                    # EXCLUDE: steps, statistics (relationships — not needed in list)
                )
            )
            .where(Workflow.database_name == database_name)
        )

        if category is not None:
            stmt = stmt.where(Workflow.category == category)
        if is_system is not None:
            stmt = stmt.where(Workflow.is_system == is_system)
        if is_active is not None:
            stmt = stmt.where(Workflow.is_active == is_active)
        if expose_as_ai_tool is not None:
            stmt = stmt.where(Workflow.expose_as_ai_tool == expose_as_ai_tool)

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def list_workflows_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        """Batch-fetch workflows by ID.

        Single SELECT ... WHERE id IN (...). Same column projection as
        list_workflows() to stay consistent with the dict shape returned
        by get_workflow(). Missing IDs are silently omitted from the result.

        Uses load_only() to prevent eager/lazy loading of relationship
        columns (steps, statistics).
        """
        self._ensure_connected()
        if not ids:
            return []
        stmt = (
            select(Workflow)
            .options(
                load_only(
                    Workflow.id,
                    Workflow.database_name,
                    Workflow.name,
                    Workflow.description,
                    Workflow.category,
                    Workflow.is_system,
                    Workflow.is_active,
                    Workflow.expose_as_ai_tool,
                    Workflow.allow_parallel_execution,
                    Workflow.timeout_seconds,
                    Workflow.max_retries,
                    Workflow.tags,
                    Workflow.icon,
                    Workflow.version,
                    Workflow.created_by,
                    Workflow.created_at,
                    Workflow.updated_at,
                    Workflow.last_executed_at,
                )
            )
            .where(Workflow.id.in_(ids))
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def get_workflow_steps(self, workflow_id: str) -> list[dict[str, Any]]:
        """Get all steps for a workflow, ordered by step_number.

        Uses load_only() to prevent lazy loading of the workflow
        relationship which would trigger an additional query per step.
        """
        self._ensure_connected()
        stmt = (
            select(WorkflowStep)
            .options(
                load_only(
                    WorkflowStep.id,
                    WorkflowStep.workflow_id,
                    WorkflowStep.step_number,
                    WorkflowStep.name,
                    WorkflowStep.description,
                    WorkflowStep.tool_type,
                    WorkflowStep.tool_id,
                    WorkflowStep.configuration,
                    WorkflowStep.condition,
                    WorkflowStep.retry_on_failure,
                    WorkflowStep.timeout_seconds,
                    WorkflowStep.depends_on,
                    WorkflowStep.continue_on_error,
                    WorkflowStep.thinking_mode,
                    WorkflowStep.created_at,
                    WorkflowStep.updated_at,
                    # EXCLUDE: workflow (relationship — prevents N+1 query per step)
                )
            )
            .where(WorkflowStep.workflow_id == workflow_id)
            .order_by(WorkflowStep.step_number)
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def get_workflow_step(self, step_id: str) -> dict[str, Any] | None:
        """Get workflow step by ID."""
        self._ensure_connected()
        step = self.session.get(WorkflowStep, step_id)
        return self._entity_to_dict(step) if step else None

    def create_workflow_step(self, step_data: dict[str, Any]) -> dict[str, Any]:
        """Create new workflow step."""
        self._ensure_connected()
        step = WorkflowStep(**step_data)
        self.session.add(step)
        self._maybe_commit()
        self.session.refresh(step)
        return self._entity_to_dict(step)

    def update_workflow_step(self, step_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update workflow step."""
        self._ensure_connected()
        step = self.session.get(WorkflowStep, step_id)
        if not step:
            msg = "WorkflowStep"
            raise NotFoundError(msg, step_id)

        for key, value in updates.items():
            setattr(step, key, value)

        step.updated_at = datetime.now(UTC)
        self.session.add(step)
        self._maybe_commit()
        self.session.refresh(step)
        return self._entity_to_dict(step)

    def delete_workflow_step(self, step_id: str) -> bool:
        """Delete workflow step."""
        self._ensure_connected()
        step = self.session.get(WorkflowStep, step_id)
        if not step:
            return False

        self.session.delete(step)
        self._maybe_commit()
        return True

    def delete_workflow_steps(self, workflow_id: str) -> int:
        """Delete all steps for a workflow. Returns count of deleted steps."""
        self._ensure_connected()
        stmt = select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id)
        steps = self.session.exec(stmt).all()
        count = len(steps)

        for step in steps:
            self.session.delete(step)

        self._maybe_commit()
        return count

    def get_workflow_statistics(self, workflow_id: str) -> dict[str, Any] | None:
        """Get statistics for a workflow."""
        self._ensure_connected()
        stats = self.session.get(WorkflowStatistics, workflow_id)
        return self._entity_to_dict(stats) if stats else None

    def create_workflow_statistics(self, stats_data: dict[str, Any]) -> dict[str, Any]:
        """Create workflow statistics."""
        self._ensure_connected()
        stats = WorkflowStatistics(**stats_data)
        self.session.add(stats)
        self._maybe_commit()
        self.session.refresh(stats)
        return self._entity_to_dict(stats)

    def update_workflow_statistics(
        self, workflow_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update workflow statistics."""
        self._ensure_connected()
        stats = self.session.get(WorkflowStatistics, workflow_id)
        if not stats:
            msg = "WorkflowStatistics"
            raise NotFoundError(msg, workflow_id)

        for key, value in updates.items():
            setattr(stats, key, value)

        stats.updated_at = datetime.now(UTC)
        self.session.add(stats)
        self._maybe_commit()
        self.session.refresh(stats)
        return self._entity_to_dict(stats)

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 8).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_workflows(self, *, database_name: str) -> int:
        """Count Workflow rows in one database."""
        self._ensure_connected()
        stmt = (
            select(func.count())
            .select_from(Workflow)
            .where(Workflow.database_name == database_name)
        )
        return int(self.session.exec(stmt).one())

    def delete_all_workflows(self, *, database_name: str) -> int:
        """Delete every Workflow row in one database."""
        self._ensure_connected()
        stmt = delete(Workflow).where(Workflow.database_name == database_name)
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)

    def clear_all_workflow_steps(self) -> int:
        """Delete every WorkflowStep row across databases."""
        self._ensure_connected()
        result = self.session.exec(delete(WorkflowStep))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def clear_all_workflow_statistics(self) -> int:
        """Delete every WorkflowStatistics row across databases."""
        self._ensure_connected()
        result = self.session.exec(delete(WorkflowStatistics))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def create_workflow_safe(self, *, workflow: dict[str, Any]) -> dict[str, Any]:
        """Create a Workflow row, raising ConflictError on duplicate name.

        See :meth:`~chaoscypher_core.ports.storage.WorkflowStorageProtocol.create_workflow_safe`
        for semantics.
        """
        from sqlalchemy.exc import IntegrityError

        from chaoscypher_core.exceptions import ConflictError

        self._ensure_connected()
        entity = Workflow(**workflow)
        self.session.add(entity)
        try:
            self._maybe_commit()
        except IntegrityError as exc:
            self.session.rollback()
            msg = f"Workflow with name '{workflow.get('name')}' already exists"
            raise ConflictError(msg, details={"name": workflow.get("name")}) from exc
        self.session.refresh(entity)
        return self._entity_to_dict(entity)
