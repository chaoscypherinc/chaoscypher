# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic models for the three-layer workflow system.

- Tools (System Tools + User Tools)
- Workflows
- Triggers.
"""

from typing import Any

from pydantic import BaseModel, Field

from chaoscypher_core.models import StepToolType
from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus
from chaoscypher_cortex.shared.api.models import PaginationMetadata
from chaoscypher_cortex.shared.models.summaries import GlobalWorkflowStatsResponse


__all__ = [
    "GlobalWorkflowStatsResponse",
    "PaginatedWorkflowsResponse",
    "WorkflowCreate",
    "WorkflowExecuteRequest",
    "WorkflowExecutionDetailResponse",
    "WorkflowExecutionResponse",
    "WorkflowExportResponse",
    "WorkflowImportRequest",
    "WorkflowImportResponse",
    "WorkflowResponse",
    "WorkflowStatsResponse",
    "WorkflowStepCreate",
    "WorkflowStepReorderRequest",
    "WorkflowStepResponse",
    "WorkflowStepUpdate",
    "WorkflowUpdate",
]


# ============================================================================
# Workflow Models
# ============================================================================


class WorkflowCreate(BaseModel):
    """Model for creating a workflow."""

    name: str
    description: str | None = None
    category: str | None = None
    expose_as_ai_tool: bool = False
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    allow_parallel_execution: bool = True
    timeout_seconds: int | None = None
    max_retries: int = 0
    tags: list[str] = Field(default_factory=list)
    icon: str | None = None


class WorkflowUpdate(BaseModel):
    """Model for updating a workflow."""

    name: str | None = None
    description: str | None = None
    category: str | None = None
    is_active: bool | None = None
    expose_as_ai_tool: bool | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    allow_parallel_execution: bool | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    tags: list[str] | None = None
    icon: str | None = None


# ============================================================================
# Workflow Step Models
# ============================================================================


class WorkflowStepCreate(BaseModel):
    """Model for creating a workflow step."""

    step_number: int
    name: str
    description: str | None = None
    tool_type: StepToolType
    tool_id: str
    configuration: dict[str, Any]
    condition: dict[str, Any] | None = None
    retry_on_failure: bool = False
    timeout_seconds: int | None = None
    depends_on: list[str] = Field(default_factory=list)
    continue_on_error: bool = False
    thinking_mode: str | None = None


class WorkflowStepUpdate(BaseModel):
    """Model for updating a workflow step."""

    step_number: int | None = None
    name: str | None = None
    description: str | None = None
    tool_type: StepToolType | None = None
    tool_id: str | None = None
    configuration: dict[str, Any] | None = None
    condition: dict[str, Any] | None = None
    retry_on_failure: bool | None = None
    timeout_seconds: int | None = None
    depends_on: list[str] | None = None
    continue_on_error: bool | None = None
    thinking_mode: str | None = None


class WorkflowStepResponse(BaseModel):
    """Response model for a workflow step."""

    id: str
    workflow_id: str
    step_number: int
    name: str
    description: str | None = None
    tool_type: str
    tool_id: str
    configuration: dict[str, Any]
    condition: dict[str, Any] | None = None
    retry_on_failure: bool
    timeout_seconds: int | None = None
    depends_on: list[str] | None = None
    continue_on_error: bool
    thinking_mode: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class WorkflowStepReorderRequest(BaseModel):
    """Request model for reordering workflow steps."""

    step_order: list[str] = Field(..., description="List of step IDs in desired order")


# ============================================================================
# Workflow Execution Models
# ============================================================================


# ``WorkflowExecutionStatus`` lives in
# ``chaoscypher_core.operations.workflows.status`` so the orchestrator
# (which writes status during execution) and the cortex API DTOs (which
# serialize status in HTTP responses) can both import from a neutral
# location. Cortex code that needs the enum imports it from runtime
# directly.


# ============================================================================
# API Response Models
# ============================================================================


class WorkflowResponse(BaseModel):
    """API response model for a workflow."""

    id: str
    database_name: str
    name: str
    description: str | None = None
    category: str | None = None
    is_system: bool
    is_active: bool
    expose_as_ai_tool: bool
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    allow_parallel_execution: bool
    timeout_seconds: int | None = None
    max_retries: int
    tags: list[str] | None = None
    icon: str | None = None
    version: str
    created_by: str | None = None
    created_at: str
    updated_at: str
    last_executed_at: str | None = None


class PaginatedWorkflowsResponse(BaseModel):
    """Paginated response for listing workflows."""

    data: list[WorkflowResponse]
    pagination: PaginationMetadata


class WorkflowExecutionResponse(BaseModel):
    """API response model for workflow execution."""

    id: str
    workflow_id: str
    triggered_by: str
    trigger_id: str | None = None
    parent_execution_id: str | None = None
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None = None
    status: WorkflowExecutionStatus
    current_step_id: str | None = None
    failed_step_id: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


class PaginatedWorkflowExecutionsResponse(BaseModel):
    """Paginated response for listing workflow executions."""

    data: list[WorkflowExecutionResponse]
    pagination: PaginationMetadata


class WorkflowExecutionDetailResponse(BaseModel):
    """API response model for workflow execution with step details."""

    id: str
    workflow_id: str
    triggered_by: str
    trigger_id: str | None = None
    parent_execution_id: str | None = None
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None = None
    status: WorkflowExecutionStatus
    current_step_id: str | None = None
    failed_step_id: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    step_executions: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowStatsResponse(BaseModel):
    """API response model for workflow stats."""

    workflow_id: str
    total_executions: int
    successful_executions: int
    failed_executions: int
    cancelled_executions: int
    success_rate: float
    avg_duration_ms: int
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    last_execution_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    updated_at: str


class WorkflowExecuteRequest(BaseModel):
    """Request model for executing a workflow."""

    inputs: dict[str, Any] = Field(default_factory=dict)
    triggered_by: str = Field(default="manual")


class WorkflowImportRequest(BaseModel):
    """Import request with options for handling duplicates and customization."""

    workflow_data: dict[str, Any] = Field(..., description="The exported workflow JSON")
    on_duplicate: str = Field(
        default="fail",
        description="How to handle duplicate workflow names: 'fail' | 'skip' | 'rename'",
    )
    new_name: str | None = Field(
        None, description="Override workflow name (useful for importing as a copy)"
    )
    import_as_inactive: bool = Field(
        default=False,
        description="Import with is_active=false for testing before activation",
    )


class WorkflowExportResponse(BaseModel):
    """API response wrapper for workflow export."""

    data: dict[str, Any] = Field(..., description="The export format")
    message: str | None = Field(None, description="Optional status message")


class WorkflowImportResponse(BaseModel):
    """Import result indicating success and any actions taken."""

    workflow_id: str = Field(..., description="Created or existing workflow ID")
    message: str = Field(..., description="Success message describing action taken")
    was_existing: bool = Field(
        default=False, description="True if duplicate was skipped (not newly created)"
    )
