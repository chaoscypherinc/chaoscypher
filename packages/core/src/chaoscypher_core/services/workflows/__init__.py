# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Management Services.

Multi-step AI research workflows with tool execution and orchestration.

The workflow system uses a refactored orchestrator pattern where execution logic
is composed from focused components (build_workflow_graph, StepExecutor, OutputManager)
rather than a monolithic WorkflowEngine class. See worker/workflow_orchestrator.py
for the orchestration functions that compose these components.

Module Organization:
- management/: Workflow CRUD and lifecycle management (create, execute, track, export)
- engine/: LangGraph execution engine (builder, state, validator, executors, output parser)
- tools/: Tool execution system (plugins, registry, executors)
- triggers/: Event-driven workflow triggers

Example:
    from chaoscypher_core.services.workflows import WorkflowService, StepExecutor, ToolService
    from chaoscypher_core.services.workflows.engine import build_workflow_graph
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    # Management operations
    adapter = SqliteAdapter(db_path="app.db")
    service = WorkflowService(storage=adapter, database_name="default")
    workflow = service.get_workflow(workflow_id)

    # Tool management (via ToolStorageProtocol)
    tool_service = ToolService(storage=adapter, database_name="default")
    tools = tool_service.list_system_tools(category="ai")

    # Build and execute LangGraph
    graph = build_workflow_graph(workflow, tool_executor, workflow_executor)

"""

# Management services (CRUD and lifecycle)
# Workflow execution engine (LangGraph)
from chaoscypher_core.services.workflows.engine import (
    BackendToolExecutorAdapter,
    OutputManager,
    ParameterInterpolator,
    # Execution components
    StepExecutor,
    # Protocols
    ToolExecutor,
    WorkflowExecutor,
    WorkflowState,
    # Validation and state
    WorkflowValidator,
    # Graph building
    build_workflow_graph,
)
from chaoscypher_core.services.workflows.management import (
    WorkflowExecutionService,
    WorkflowPortabilityService,
    WorkflowService,
    WorkflowStepsService,
)

# Tool system (plugins, registry, executor)
from chaoscypher_core.services.workflows.tools import (
    # Plugin system
    BaseToolPlugin,
    ToolExecutionContext,
    ToolRegistry,
    # Tool service
    ToolService,
)


__all__ = [
    "BackendToolExecutorAdapter",
    "BaseToolPlugin",
    "OutputManager",
    "ParameterInterpolator",
    # Engine
    "StepExecutor",
    "ToolExecutionContext",
    "ToolExecutor",
    "ToolRegistry",
    # Tools
    "ToolService",
    "WorkflowExecutionService",
    "WorkflowExecutor",
    "WorkflowPortabilityService",
    # Management
    "WorkflowService",
    "WorkflowState",
    "WorkflowStepsService",
    "WorkflowValidator",
    "build_workflow_graph",
]
