# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflows Feature.

AI-powered workflow execution and management system.

This feature provides comprehensive workflow orchestration capabilities including
creation, execution, monitoring, and portability. Uses refactored orchestrator pattern
with LangGraph for stateful AI agent execution with tool calling, memory, and multi-step
reasoning. Supports both interactive and background execution modes via the queue system.

Components:
- WorkflowService: Engine service for workflow CRUD operations (from chaoscypher_core)
- WorkflowExecutionRepository: Execution history and state tracking
- WorkflowPortabilityService: Import/export workflows in portable format
- orchestrator: Standalone orchestration functions (execute_workflow_task, execute_step_task)

Architecture:
Core execution logic uses refactored components from engine/services/workflow
(build_workflow_graph, StepExecutor, OutputManager) composed via standalone orchestrator
functions. Factory pattern provides dependency injection for session, adapters, and services.

Example:
    from chaoscypher_core.services.workflows import WorkflowService
    from chaoscypher_core.operations.workflows.orchestrator import execute_workflow_task

    # Create and execute workflow
    service = WorkflowService(storage=adapter, database_name=db_name, tool_service=tool_svc)
    workflow = service.create_workflow("Research Task", {...})
    result = await execute_workflow_task(workflow.id, inputs, **services)

"""

from chaoscypher_core.operations.workflows.repository import (
    WorkflowExecutionRepository,
)
from chaoscypher_core.services.workflows.management.io import WorkflowPortabilityService
from chaoscypher_cortex.features.workflows.api import router
from chaoscypher_cortex.features.workflows.execution_api import router as execution_router


__all__ = [
    "WorkflowExecutionRepository",
    "WorkflowPortabilityService",
    "execution_router",
    "router",
]
