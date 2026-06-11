# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflows API Endpoints — CRUD, Import/Export, Steps, Triggers.

GET    /api/v1/workflows - List workflows (paginated)
POST   /api/v1/workflows - Create workflow
GET    /api/v1/workflows/{id} - Get workflow
PATCH  /api/v1/workflows/{id} - Update workflow
DELETE /api/v1/workflows/{id} - Delete workflow
POST   /api/v1/workflows/{id}/duplicate - Duplicate workflow
GET    /api/v1/workflows/{id}/export - Export workflow to JSON
POST   /api/v1/workflows/import - Import workflow from JSON
GET    /api/v1/workflows/{id}/triggers - List triggers for workflow

Sub-routers (registered in main.py):
- execution_api: Workflow execution, history, stats
"""

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.factories import (
    get_trigger_service as _make_trigger_service,
)
from chaoscypher_core.factories import (
    get_workflow_service as _make_workflow_service,
)
from chaoscypher_cortex.features.workflows.models import (
    GlobalWorkflowStatsResponse,
    PaginatedWorkflowsResponse,
    WorkflowCreate,
    WorkflowExportResponse,
    WorkflowImportRequest,
    WorkflowImportResponse,
    WorkflowResponse,
    WorkflowStepCreate,
    WorkflowStepReorderRequest,
    WorkflowStepResponse,
    WorkflowStepUpdate,
    WorkflowUpdate,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
    paginate_list,
)
from chaoscypher_cortex.shared.api.errors import (
    raise_if_not_found,
    resource_not_found_error,
    validation_error,
)
from chaoscypher_cortex.shared.api.models import TriggerSummaryResponse
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    ErrorDetail,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import TriggerDict, WorkflowDict, WorkflowStepDict
    from chaoscypher_core.services.workflows.management import (
        WorkflowService as EngineWorkflowService,
    )
    from chaoscypher_core.services.workflows.management import WorkflowStepsService
    from chaoscypher_core.services.workflows.triggers import TriggerService

# Create router
router = APIRouter()


# Dependency to get workflow service
def get_engine_workflow_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> EngineWorkflowService:
    """Get WorkflowService instance using shared factory."""
    return _make_workflow_service(settings.current_database)


# Dependency to get workflow steps service
def get_workflow_steps_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WorkflowStepsService:
    """Get WorkflowStepsService instance (uses chaoscypher service with SQLite adapter)."""
    from chaoscypher_core.database import get_sqlite_adapter
    from chaoscypher_core.services.workflows.management import (
        WorkflowStepsService as EngineStepsService,
    )

    # Get singleton storage adapter
    adapter = get_sqlite_adapter(database_name=settings.current_database)

    # Create chaoscypher steps service (adapter implements WorkflowStorageProtocol)
    return EngineStepsService(repository=adapter)


# Dependency to get trigger service (for workflow triggers endpoint)
def get_workflow_trigger_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TriggerService:
    """Get TriggerService instance using shared factory."""
    return _make_trigger_service(settings.current_database)


# ============================================================================
# Stats Endpoints (moved here to avoid routing conflicts)
# ============================================================================


@router.get(
    "/stats",
    response_model=GlobalWorkflowStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_global_stats(
    _: CurrentUsername,
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
) -> dict[str, Any]:
    """Get global workflow stats.

    Returns aggregated stats across all workflows.
    """
    return workflow_service.get_global_stats()


# ============================================================================
# Workflow CRUD Endpoints
# ============================================================================


@router.get(
    "",
    response_model=PaginatedWorkflowsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_workflows(
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    pagination: PageParams,
    _: CurrentUsername,
    category: str | None = Query(None, description="Filter by category"),
    is_system: bool | None = Query(None, description="Filter by system flag"),
    is_active: bool | None = Query(None, description="Filter by active flag"),
    expose_as_ai_tool: bool | None = Query(None, description="Filter by AI tool exposure"),
) -> PaginatedWorkflowsResponse:
    """List all workflows with optional filters and pagination.

    - Single-user mode: the local operator owns everything.
    """
    page, page_size = pagination
    all_workflows = workflow_service.list_workflows(
        category=category,
        is_system=is_system,
        is_active=is_active,
        expose_as_ai_tool=expose_as_ai_tool,
    )
    result = paginate_list(all_workflows, page, page_size)
    return PaginatedWorkflowsResponse(
        data=[WorkflowResponse(**w) for w in result["data"]],
        pagination=result["pagination"],
    )


@router.post(
    "",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **CONFLICT_RESPONSE,
    },
)
async def create_workflow(
    workflow_create: WorkflowCreate,
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> WorkflowDict:
    """Create a new workflow.

    - Single-user mode: the local operator owns everything.
    """
    workflow_id = workflow_service.create_workflow(workflow_create.model_dump())
    created_workflow = workflow_service.get_workflow(workflow_id)
    if not created_workflow:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorDetail(
                code="OPERATION_FAILED", message="Failed to create workflow"
            ).model_dump(),
        )
    return created_workflow


@router.get(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_workflow(
    workflow_id: str,
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> WorkflowDict:
    """Get workflow by ID.

    - Single-user mode: the local operator owns everything.
    """
    workflow = workflow_service.get_workflow(workflow_id)
    return raise_if_not_found(workflow, f"Workflow not found: {workflow_id}")


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_workflow(
    workflow_id: str,
    workflow_update: WorkflowUpdate,
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> WorkflowDict:
    """Update an existing workflow.

    Cannot update system workflows.

    - Single-user mode: the local operator owns everything.
    """
    success = workflow_service.update_workflow(
        workflow_id, workflow_update.model_dump(exclude_unset=True)
    )
    raise_if_not_found(success, f"Workflow {workflow_id} not found")
    updated_workflow = workflow_service.get_workflow(workflow_id)
    if not updated_workflow:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorDetail(
                code="OPERATION_FAILED", message="Failed to fetch updated workflow"
            ).model_dump(),
        )
    return updated_workflow


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_workflow(
    workflow_id: str,
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> Response:
    """Delete a workflow.

    Cannot delete system workflows.

    - Single-user mode: the local operator owns everything.
    """
    success = workflow_service.delete_workflow(workflow_id)
    raise_if_not_found(success, f"Workflow not found: {workflow_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================================
# Duplicate Endpoint
# ============================================================================


@router.post(
    "/{workflow_id}/duplicate",
    response_model=WorkflowImportResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def duplicate_workflow(
    workflow_id: str,
    service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> WorkflowImportResponse:
    """Duplicate a workflow with all its steps.

    Exports the workflow and re-imports it with a renamed copy. The new
    workflow name is "{original_name} (imported)", with a numeric suffix
    (e.g. "{original_name} (imported) (2)") when that name is taken too.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - 201 Created with duplicated workflow ID

    **Raises:**
    - 404: Workflow not found
    """
    try:
        export_data = service.export_workflow(workflow_id)
    except ValueError as e:
        raise resource_not_found_error("workflow", workflow_id) from e

    result = service.import_workflow(
        workflow_data=export_data,
        on_duplicate="rename",
    )
    return WorkflowImportResponse(**result)


# ============================================================================
# Export/Import Endpoints
# ============================================================================


@router.get(
    "/{workflow_id}/export",
    response_model=WorkflowExportResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def export_workflow(
    workflow_id: str,
    service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> WorkflowExportResponse:
    """Export a workflow with all its steps to portable JSON format.

    **RESTful Design:**
    - GET /export is correct for read-only export operation
    - No side effects, just returns workflow data

    **Auth:** Optional (grounding_api_key or no auth)

    **Returns:**
    - Export data in WorkflowExportFormat structure
    - Includes workflow metadata and all steps
    - Can be imported into any database

    **Use Case:**
    - Backup workflows
    - Share workflows between databases
    - Template distribution

    **Example:**
    ```bash
    curl -X GET http://localhost:8080/api/v1/workflows/{id}/export
    ```
    """
    try:
        export_data = service.export_workflow(workflow_id)
        return WorkflowExportResponse(data=export_data, message="Workflow exported successfully")
    except ValueError as e:
        raise resource_not_found_error("workflow", workflow_id) from e


@router.post(
    "/import",
    response_model=WorkflowImportResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **CONFLICT_RESPONSE,
    },
)
async def import_workflow(
    import_request: WorkflowImportRequest,
    service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> WorkflowImportResponse:
    """Import a workflow from exported JSON format.

    **Auth:** Optional (grounding_api_key or no auth)

    **Request Body:**
    - `workflow_data`: Exported workflow JSON (from export endpoint)
    - `on_duplicate`: How to handle duplicate names: "fail" | "skip" | "rename" (default: "fail")
    - `new_name`: Optional override for workflow name
    - `import_as_inactive`: Import with is_active=False (default: false)

    **Returns:**
    - Created workflow ID
    - Success message
    - Whether existing workflow was used (skip case)

    **Use Case:**
    - Restore workflows from backup
    - Import workflows from other databases
    - Install workflow templates

    **Example:**
    ```bash
    curl -X POST http://localhost:8080/api/v1/workflows/import \
      -H "Content-Type: application/json" \
      -d '{"workflow_data": {...}, "on_duplicate": "rename"}'
    ```
    """
    try:
        result = service.import_workflow(
            workflow_data=import_request.workflow_data,
            on_duplicate=import_request.on_duplicate,
            new_name=import_request.new_name,
            import_as_inactive=import_request.import_as_inactive,
        )
        return WorkflowImportResponse(**result)
    except ValueError as e:
        raise validation_error("workflow_import", internal_error=e) from e


# ============================================================================
# Workflow Steps CRUD Endpoints
# ============================================================================


@router.get(
    "/{workflow_id}/steps",
    response_model=list[WorkflowStepResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def list_workflow_steps(
    workflow_id: str,
    steps_service: Annotated[WorkflowStepsService, Depends(get_workflow_steps_service)],
    _: CurrentUsername,
) -> list[WorkflowStepDict]:
    """List all steps for a workflow.

    Returns steps sorted by step_number.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - List of workflow steps

    **Raises:**
    - 404: Workflow not found
    - 403: Access denied
    """
    return steps_service.list_steps(workflow_id)


@router.post(
    "/{workflow_id}/steps",
    response_model=WorkflowStepResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def create_workflow_step(
    workflow_id: str,
    step_create: WorkflowStepCreate,
    steps_service: Annotated[WorkflowStepsService, Depends(get_workflow_steps_service)],
    _: CurrentUsername,
) -> WorkflowStepDict:
    """Create a new workflow step.

    - Single-user mode: the local operator owns everything.

    **Request Body:**
    - step_number: Optional - auto-increments if not provided
    - name: Step name (required)
    - tool_type: "system" or "user" (required)
    - tool_id: ID of tool to execute (required)
    - configuration: Parameter mappings (required)
    - ... (see WorkflowStepCreate model)

    **Returns:**
    - 201 Created with step details

    **Raises:**
    - 404: Workflow not found
    - 403: Cannot modify system workflows
    - 400: Validation error
    """
    return steps_service.create_step(workflow_id, step_create.model_dump())


@router.get(
    "/{workflow_id}/steps/{step_id}",
    response_model=WorkflowStepResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_workflow_step(
    workflow_id: str,
    step_id: str,
    steps_service: Annotated[WorkflowStepsService, Depends(get_workflow_steps_service)],
    _: CurrentUsername,
) -> WorkflowStepDict:
    """Get a specific workflow step.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - Step details

    **Raises:**
    - 404: Workflow or step not found
    - 403: Access denied
    """
    return steps_service.get_step(workflow_id, step_id)


@router.patch(
    "/{workflow_id}/steps/{step_id}",
    response_model=WorkflowStepResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_workflow_step(
    workflow_id: str,
    step_id: str,
    step_update: WorkflowStepUpdate,
    steps_service: Annotated[WorkflowStepsService, Depends(get_workflow_steps_service)],
    _: CurrentUsername,
) -> WorkflowStepDict:
    """Update an existing workflow step.

    - Single-user mode: the local operator owns everything.

    **Request Body:**
    - Partial update - only include fields to change
    - See WorkflowStepUpdate model for available fields

    **Returns:**
    - Updated step details

    **Raises:**
    - 404: Workflow or step not found
    - 403: Cannot modify system workflows
    - 400: Validation error
    """
    return steps_service.update_step(
        workflow_id, step_id, step_update.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{workflow_id}/steps/{step_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_workflow_step(
    workflow_id: str,
    step_id: str,
    steps_service: Annotated[WorkflowStepsService, Depends(get_workflow_steps_service)],
    _: CurrentUsername,
) -> Response:
    """Delete a workflow step.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - 204 No Content on success

    **Raises:**
    - 404: Workflow or step not found
    - 403: Cannot modify system workflows
    """
    steps_service.delete_step(workflow_id, step_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{workflow_id}/steps/reorder",
    response_model=list[WorkflowStepResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def reorder_workflow_steps(
    workflow_id: str,
    reorder_request: WorkflowStepReorderRequest,
    steps_service: Annotated[WorkflowStepsService, Depends(get_workflow_steps_service)],
    _: CurrentUsername,
) -> list[WorkflowStepDict]:
    """Reorder workflow steps.

    Updates the step_number field for all steps based on the provided order.

    - Single-user mode: the local operator owns everything.

    **Request Body:**
    - step_order: List of step IDs in desired order (required)
    - Must include ALL step IDs for the workflow

    **Returns:**
    - List of reordered steps

    **Raises:**
    - 404: Workflow not found
    - 403: Cannot modify system workflows
    - 400: Invalid step order (missing/extra step IDs)
    """
    return steps_service.reorder_steps(workflow_id, reorder_request.step_order)


# ============================================================================
# Workflow Triggers Endpoint
# ============================================================================


@router.get(
    "/{workflow_id}/triggers",
    response_model=list[TriggerSummaryResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def list_workflow_triggers(
    workflow_id: str,
    trigger_service: Annotated[TriggerService, Depends(get_workflow_trigger_service)],
    workflow_service: Annotated[EngineWorkflowService, Depends(get_engine_workflow_service)],
    _: CurrentUsername,
) -> list[TriggerDict]:
    """List all triggers for a specific workflow.

    Returns triggers that are configured to execute this workflow when their
    event conditions are met.

    - Single-user mode: the local operator owns everything.

    **Returns:**
    - List of triggers for the workflow

    **Raises:**
    - 404: Workflow not found
    - 403: Access denied
    """
    # Verify workflow exists first
    workflow = workflow_service.get_workflow(workflow_id)
    raise_if_not_found(workflow, f"Workflow not found: {workflow_id}")

    # Return triggers filtered by workflow_id
    return trigger_service.list_triggers(workflow_id=workflow_id)
