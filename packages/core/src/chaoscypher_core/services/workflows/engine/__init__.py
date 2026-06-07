# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Execution Engine - LangGraph State Machine.

Framework-agnostic workflow execution engine using LangGraph state machines.
Provides validation, state management, step execution, and graph building without backend dependencies.
Supports both async (backend) and sync (CLI) modes.

Components:
- Validation: WorkflowValidator for structure/input validation
- Interpolation: ParameterInterpolator for template variable resolution
- Output Management: OutputManager for schema-based output mapping with logging
- Step Execution: StepExecutor for plugin-based workflow step execution
- Tool Adapter: BackendToolExecutorAdapter bridges backend services to engine Protocols
- LangGraph Integration: Full workflow execution with state machine
- State Management: WorkflowState
- Graph Building: build_workflow_graph for creating LangGraph workflows

Example:
    from chaoscypher_core.services.workflows.engine import (
        build_workflow_graph,
        WorkflowValidator,
        StepExecutor,
    )

    # Validate workflow
    errors = WorkflowValidator.validate_workflow(workflow_def)

    # Build LangGraph
    graph = build_workflow_graph(workflow_def, tool_executor, workflow_executor)

    # Execute individual steps
    executor = StepExecutor(tool_executor_adapter, llm_provider)
    result = await executor.execute_step(step_config, inputs, context)

"""

from chaoscypher_core.services.workflows.engine.builder import build_workflow_graph
from chaoscypher_core.services.workflows.engine.executor import (
    ToolExecutor,
    WorkflowExecutor,
    create_error_handler_node,
    create_tool_execution_node,
)
from chaoscypher_core.services.workflows.engine.interpolator import ParameterInterpolator
from chaoscypher_core.services.workflows.engine.output_parser import OutputManager
from chaoscypher_core.services.workflows.engine.state import WorkflowState
from chaoscypher_core.services.workflows.engine.step_executor import StepExecutor
from chaoscypher_core.services.workflows.engine.tool_executor_adapter import (
    BackendToolExecutorAdapter,
)
from chaoscypher_core.services.workflows.engine.validator import WorkflowValidator


__all__ = [
    "BackendToolExecutorAdapter",
    "OutputManager",
    "ParameterInterpolator",
    # Step execution
    "StepExecutor",
    # Protocols
    "ToolExecutor",
    "WorkflowExecutor",
    # State management
    "WorkflowState",
    # Validation and utilities
    "WorkflowValidator",
    # LangGraph integration
    "build_workflow_graph",
    "create_error_handler_node",
    "create_tool_execution_node",
]
