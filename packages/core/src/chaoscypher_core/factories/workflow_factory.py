# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Service Factory - Shared factory for WorkflowService construction.

Provides a single factory function to create properly configured WorkflowService
instances. Used by both cortex API (features/workflows/api.py) and neuron worker.

Example:
    from chaoscypher_core.factories import get_workflow_service

    service = get_workflow_service("my_database")

"""

from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.factories.tool_factory import get_tool_service
from chaoscypher_core.services.workflows.management import (
    WorkflowService as EngineWorkflowService,
)


def get_workflow_service(database_name: str) -> EngineWorkflowService:
    """Create WorkflowService with proper dependency injection.

    Uses SqliteAdapter which implements WorkflowStorageProtocol via WorkflowsMixin.

    Args:
        database_name: Current database name for workflow storage.

    Returns:
        Configured WorkflowService instance ready for use.

    """
    adapter = get_sqlite_adapter(database_name=database_name)
    tool_service = get_tool_service(database_name)

    return EngineWorkflowService(
        storage=adapter,
        database_name=database_name,
        tool_service=tool_service,
    )
