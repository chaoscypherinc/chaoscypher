# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Execution Engine.

Event-driven workflow trigger execution with automatic dispatch.

Components:
- TriggerExecutor: Dispatches workflows based on event triggers

Example:
    from chaoscypher_core.services.workflows.triggers.engine import TriggerExecutor

    # Create executor (execute_workflow_fn injected to avoid circular dependency)
    executor = TriggerExecutor(
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        tool_service=tool_service,
        llm_service=llm_service,
        graph_repository=graph_repo,
        search_repository=search_repo,
        database_name="my_database",
        execute_workflow_fn=execute_workflow_task,
    )

    # Start listening for events
    await executor.start()

    # Dispatch event
    await executor.dispatch_event("node.create", {"entity_id": "...", ...})

"""

from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor


__all__ = [
    "TriggerExecutor",
]
