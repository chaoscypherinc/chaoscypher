# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Execution API Endpoints.

POST   /api/v1/workflows/{id}/executions - Execute a workflow
GET    /api/v1/workflows/{id}/executions - List workflow executions
GET    /api/v1/workflows/{id}/executions/{eid} - Get execution details
POST   /api/v1/workflows/{id}/executions/{eid}/cancel - Cancel execution
GET    /api/v1/workflows/{id}/stats - Get workflow execution stats.
"""

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.workflows.models import (
    PaginatedWorkflowExecutionsResponse,
    WorkflowExecuteRequest,
    WorkflowExecutionDetailResponse,
    WorkflowExecutionResponse,
    WorkflowStatsResponse,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
    paginate_list,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.database import get_current_session


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.management import WorkflowExecutionService

# Create router
router = APIRouter()


# ============================================================================
# Dependency Injection
# ============================================================================


def get_execution_service(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WorkflowExecutionService:
    """Get WorkflowExecutionService instance (VSA pattern).

    Properly instantiates all dependencies using SqliteAdapter.
    """
    from chaoscypher_core.database import get_sqlite_adapter
    from chaoscypher_core.operations import OperationsRepository
    from chaoscypher_core.services.workflows.management.history import (
        WorkflowExecutionService,
    )

    # Get singleton storage adapter (implements both WorkflowStorageProtocol and WorkflowExecutionStorageProtocol)
    adapter = get_sqlite_adapter(database_name=settings.current_database)

    # Get repositories
    from chaoscypher_core.repo_factories import (
        get_graph_repository,
        get_search_repository,
    )

    get_search_repository(database_name=settings.current_database)
    graph_repo = get_graph_repository(session, settings.current_database)

    # Initialize LLM service using singleton factories
    from chaoscypher_core.llm_queue import (
        get_llm_queue_service,
        get_provider_factory,
    )

    factory = get_provider_factory()  # Singleton
    factory.get_chat_provider()  # Cached
    get_llm_queue_service()  # Singleton

    # Initialize operations repository
    operations_repo = OperationsRepository(graph_repository=graph_repo, settings=settings)

    # Use adapter for execution tracking (implements WorkflowExecutionStorageProtocol via WorkflowExecutionsMixin)
    return WorkflowExecutionService(
        repository=adapter,
        execution_repository=adapter,  # Same adapter - implements both protocols (ISP-compliant)
        operations_service=operations_repo,
        stats_max_executions=settings.pagination.stats_max_executions,
    )


# ============================================================================
# Workflow Execution Endpoints
# ============================================================================


@router.post(
    "/{workflow_id}/executions",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
)
async def execute_workflow(
    workflow_id: str,
    request: WorkflowExecuteRequest,
    execution_service: Annotated[WorkflowExecutionService, Depends(get_execution_service)],
    _: CurrentUsername,
) -> dict[str, str]:
    """Execute a workflow asynchronously.

    Queues the workflow for execution and returns immediately with an execution ID.
    Use the execution ID to poll for status and results.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - 202 Accepted with execution_id
    """
    execution_id = await execution_service.execute_workflow(
        workflow_id=workflow_id,
        inputs=request.inputs,
        triggered_by=request.triggered_by,
        user=None,
    )

    return {
        "execution_id": execution_id,
        "status": "queued",
        "message": "Workflow execution queued successfully",
    }


@router.get(
    "/{workflow_id}/executions",
    response_model=PaginatedWorkflowExecutionsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def list_workflow_executions(
    workflow_id: str,
    execution_service: Annotated[WorkflowExecutionService, Depends(get_execution_service)],
    pagination: PageParams,
    _: CurrentUsername,
    status: str | None = Query(
        None,
        description="Filter by status (pending, running, completed, failed, cancelled)",
    ),
) -> PaginatedWorkflowExecutionsResponse:
    """List execution history for a workflow with page-based pagination.

    - Single-user mode: the local operator owns everything.

    **Query Parameters:**
    - page: Page number (1-indexed, default 1)
    - page_size: Items per page (default 50, max 1000)
    - status: Optional status filter
    """
    page, page_size = pagination
    # Service still uses skip/limit; translate page/page_size accordingly.
    # Fetch ample rows then slice via paginate_list for the canonical envelope.
    all_rows = execution_service.get_executions(
        workflow_id=workflow_id,
        limit=get_settings().pagination.workflow_executions_fetch_limit,
        skip=0,
        status_filter=status,
        user=None,
    )
    result = paginate_list(all_rows, page, page_size)
    return PaginatedWorkflowExecutionsResponse(
        data=[WorkflowExecutionResponse(**r) for r in result["data"]],
        pagination=result["pagination"],
    )


@router.get(
    "/{workflow_id}/executions/{execution_id}",
    response_model=WorkflowExecutionDetailResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_workflow_execution(
    workflow_id: str,
    execution_id: str,
    execution_service: Annotated[WorkflowExecutionService, Depends(get_execution_service)],
    _: CurrentUsername,
) -> dict[str, Any]:
    """Get detailed execution information.

    Returns full execution details including all step executions.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - Execution details with step_executions array

    **Raises:**
    - 404: Workflow or execution not found
    - 403: Access denied
    """
    return execution_service.get_execution(
        workflow_id=workflow_id,
        execution_id=execution_id,
        user=None,
    )


@router.post(
    "/{workflow_id}/executions/{execution_id}/cancel",
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def cancel_workflow_execution(
    workflow_id: str,
    execution_id: str,
    execution_service: Annotated[WorkflowExecutionService, Depends(get_execution_service)],
    _: CurrentUsername,
) -> dict[str, Any]:
    """Cancel a running workflow execution.

    Attempts to gracefully cancel a running or queued execution.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - Cancellation result with new status

    **Raises:**
    - 404: Workflow or execution not found
    - 403: Access denied
    - 400: Execution already completed/failed/cancelled
    """
    return await execution_service.cancel_execution(
        workflow_id=workflow_id,
        execution_id=execution_id,
        user=None,
    )


@router.get(
    "/{workflow_id}/stats",
    response_model=WorkflowStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_workflow_stats(
    workflow_id: str,
    execution_service: Annotated[WorkflowExecutionService, Depends(get_execution_service)],
    _: CurrentUsername,
) -> dict[str, Any]:
    """Get workflow execution stats.

    Returns aggregate stats including:
    - Total executions
    - Success/failure/cancelled counts
    - Success rate
    - Average execution time
    - Last execution timestamp

    - Single-user mode: the local operator owns everything.

    **Raises:**
    - 404: Workflow not found
    - 403: Access denied
    """
    return execution_service.get_stats(
        workflow_id=workflow_id,
        user=None,
    )
