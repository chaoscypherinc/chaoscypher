# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow System Reset Service.

Handles reset operations for workflow system components:
- Workflows and workflow steps
- System tools and user tools
- Triggers
- All related statistics

Extracted from ResetService to follow Single Responsibility Principle.
"""

from typing import Any

import structlog

from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
from chaoscypher_core.database.seed import (
    seed_default_triggers,
    seed_default_workflows,
    seed_system_tools,
)


logger = structlog.get_logger(__name__)


class WorkflowSystemResetService:
    """Reset service for workflow system components.

    Responsibility: Reset workflows, tools, and triggers to default state.
    """

    def __init__(self, database_name: str):
        """Initialize workflow system reset service.

        Args:
            database_name: Name of the database

        """
        self.database_name = database_name

    def reset_all_components(self) -> dict[str, Any]:
        """Reset entire workflow system to defaults.

        Drops and reseeds:
        - Workflows and workflow steps
        - System tools and user tools
        - Triggers
        - All statistics

        Returns:
            Dictionary with reset statistics

        """
        logger.info("workflow_system_reset_started", database_name=self.database_name)

        adapter = get_sqlite_adapter(database_name=self.database_name)
        try:
            with adapter.transaction():
                # Count before deletion
                workflow_count = adapter.count_workflows(database_name=self.database_name)
                user_tool_count = adapter.count_user_tools(database_name=self.database_name)
                trigger_count = adapter.count_triggers(database_name=self.database_name)
                # Execution counts aren't on dedicated protocol methods yet;
                # use list-length as a cheap approximation. For reset stats
                # this is good enough.
                workflow_execution_count = (
                    len(adapter.list_active_executions(workflow_id=""))
                    if hasattr(adapter, "list_active_executions")
                    else 0
                )
                # TriggerExecutionRow count: no dedicated count method,
                # but clear_all returns the rowcount.

                # Delete all workflow-system data in FK order
                # 1. Execution history first (FKs into triggers + workflows)
                trigger_execution_count = adapter.clear_all_trigger_executions()
                adapter.clear_all_workflow_executions()
                adapter.clear_all_workflow_statistics()
                adapter.clear_all_workflow_steps()
                # 2. Workflows (scoped) then tools (system_tools are global)
                adapter.delete_all_workflows(database_name=self.database_name)
                adapter.clear_all_tool_statistics()
                adapter.delete_all_user_tools(database_name=self.database_name)
                adapter.clear_all_system_tools()
                adapter.delete_all_triggers(database_name=self.database_name)

                # Reseed defaults (inside the same transaction — no mid-method commit)
                session = adapter.session
                assert session is not None, "adapter.session required for reseed"
                seed_system_tools(session)
                seed_default_workflows(session, self.database_name)
                seed_default_triggers(session, self.database_name)

                # Count what was created after reseed
                workflows_created = adapter.count_workflows(database_name=self.database_name)
                system_tools_created = adapter.count_system_tools()
                triggers_created = adapter.count_triggers(database_name=self.database_name)
        finally:
            adapter.disconnect()

        logger.info("workflow_system_reset_complete")

        return {
            "status": "success",
            "workflows_deleted": workflow_count,
            "workflow_executions_deleted": workflow_execution_count,
            "user_tools_deleted": user_tool_count,
            "triggers_deleted": trigger_count,
            "trigger_executions_deleted": trigger_execution_count,
            "workflows_created": workflows_created,
            "system_tools_created": system_tools_created,
            "triggers_created": triggers_created,
        }
