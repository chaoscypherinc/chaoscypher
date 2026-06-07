# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Templates API Endpoints.

GET    /api/v1/templates - List templates
POST   /api/v1/templates - Create template
GET    /api/v1/templates/{id} - Get template
PATCH  /api/v1/templates/{id} - Update template
DELETE /api/v1/templates/{id} - Delete template
POST   /api/v1/templates/batch - Batch operations.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import Session

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.models import (
    TemplateCreate,
    TemplateUpdate,
)
from chaoscypher_core.services.graph.management.template import TemplateService
from chaoscypher_cortex.features.templates.models import (
    PaginatedTemplatesResponse,
    QueuedEmbeddingRegenResponse,
    TemplateResponse,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
)
from chaoscypher_cortex.shared.api.errors import create_error_response
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    RATE_LIMIT_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.database import get_current_session
from chaoscypher_cortex.shared.kernel import BulkRequest, BulkResponse


router = APIRouter()


def get_template_service(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TemplateService:
    """Get TemplateService instance (uses engine service directly)."""
    from chaoscypher_core.repo_factories import get_graph_repository

    graph_repository = get_graph_repository(session, settings.current_database)
    return TemplateService(graph_repository=graph_repository)


@router.get(
    "",
    response_model=PaginatedTemplatesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_templates(
    _: CurrentUsername,
    template_service: Annotated[TemplateService, Depends(get_template_service)],
    pagination: PageParams,
    template_type: str | None = Query(None, description="Filter by type (node/edge)"),
) -> PaginatedTemplatesResponse:
    """List all templates with pagination.

    **Query Parameters:**
    - `template_type`: Filter by 'node' or 'edge' (optional)
    - `page`: Page number (default: 1)
    - `page_size`: Items per page

    **Returns:**
    - Paginated list of templates with metadata
    """
    page, page_size = pagination
    result = template_service.list_templates(
        template_type=template_type, page=page, page_size=page_size
    )
    # Convert dict results to Pydantic models
    return PaginatedTemplatesResponse(
        data=[TemplateResponse(**template_dict) for template_dict in result["data"]],
        pagination=result["pagination"],
    )


@router.post(
    "",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def create_template(
    _: CurrentUsername,
    template_create: TemplateCreate,
    template_service: Annotated[TemplateService, Depends(get_template_service)],
) -> TemplateResponse:
    """Create a new template.

    **Request Body:**
    - `name`: Template name (cannot start with 'system_')
    - `description`: Template description (optional)
    - `template_type`: 'node' or 'edge'
    - `properties`: List of property definitions

    **Returns:**
    - Created template with generated ID

    **Errors:**
    - 400: Invalid template name (system prefix)
    """
    template_dict = template_service.create_template(template_create)
    return TemplateResponse(**template_dict)


@router.get(
    "/{template_id}",
    response_model=TemplateResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_template(
    _: CurrentUsername,
    template_id: str,
    template_service: Annotated[TemplateService, Depends(get_template_service)],
) -> TemplateResponse:
    """Get a specific template by ID.

    **Errors:**
    - 404: Template not found
    """
    template_dict = template_service.get_template(template_id)
    return TemplateResponse(**template_dict)


@router.patch(
    "/{template_id}",
    response_model=TemplateResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_template(
    _: CurrentUsername,
    template_id: str,
    template_update: TemplateUpdate,
    template_service: Annotated[TemplateService, Depends(get_template_service)],
) -> TemplateResponse:
    """Update an existing template.

    **Request Body:**
    - `name`: New name (optional, cannot start with 'system_')
    - `description`: New description (optional)
    - `properties`: New property definitions (optional)

    **Errors:**
    - 400: Invalid template name (system prefix)
    - 404: Template not found
    """
    template_dict = template_service.update_template(template_id, template_update)
    return TemplateResponse(**template_dict)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
)
async def delete_template(
    _: CurrentUsername,
    template_id: str,
    template_service: Annotated[TemplateService, Depends(get_template_service)],
    force: bool = Query(False, description="Delete even if in use"),
) -> Response:
    """Delete a template.

    **Query Parameters:**
    - `force`: If True, delete even if nodes/edges are using it (default: False)

    **Returns:**
    - 204 No Content on success

    **Errors:**
    - 404: Template not found
    - 409: Template is in use (nodes or edges reference it)
    """
    try:
        template_service.delete_template(template_id, force=force)
    except ValueError as e:
        # Template is in use by nodes or edges
        raise create_error_response(
            status_code=status.HTTP_409_CONFLICT,
            error_code="TEMPLATE_IN_USE",
            public_message="Template cannot be deleted because it is in use",
            internal_error=e,
            log_level="warning",
        ) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/embeddings",
    response_model=QueuedEmbeddingRegenResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **RATE_LIMIT_RESPONSE,
    },
)
async def regenerate_template_embeddings(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueuedEmbeddingRegenResponse:
    """Regenerate embeddings for all templates.

    Queues a background job to generate embeddings for all templates,
    enabling semantic template search (e.g., search "people" to find "character" template).

    **Use Cases:**
    - After importing new templates
    - When embeddings are missing or outdated
    - When changing embedding model

    **Returns:**
    - `task_id`: Unique identifier for tracking the job
    - `status`: "queued" indicating the task has been accepted
    - `message`: Confirmation message

    **Status Code:** 202 Accepted

    **Tracking Results:**
    - Use `GET /api/v1/queue/tasks/{task_id}` to check operation status
    """
    from chaoscypher_core.constants import QUEUE_LLM
    from chaoscypher_core.queue import queue_client

    task_id = await queue_client.enqueue_task(
        queue=QUEUE_LLM,  # Use LLM queue since embeddings require LLM provider
        operation="regenerate_template_embeddings",
        data={"database_name": settings.current_database},
        priority=settings.priorities.background,
        metadata={"operation_type": "template_embeddings"},
    )

    return QueuedEmbeddingRegenResponse(
        task_id=task_id,
        status="queued",
        message="Template embedding regeneration started",
    )


@router.post(
    "/batch",
    response_model=BulkResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **RATE_LIMIT_RESPONSE,
    },
)
async def batch_templates_operation(
    _: CurrentUsername,
    request: BulkRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BulkResponse:
    """Queue batch operations on templates.

    This endpoint accepts a list of operations (create, update, delete) to be performed
    on templates in a single batch. The operations are queued for background processing.

    **Request Body:**
    - `operations`: List of operations, each with:
      - `operation`: Operation type ('create', 'update', or 'delete')
      - `data`: Operation-specific data (template definition for create/update, template_id for delete)

    **Example Request:**
    ```json
    {
        "operations": [
            {
                "operation": "create",
                "data": {
                    "name": "Person",
                    "description": "A person entity",
                    "template_type": "node",
                    "properties": [
                        {
                            "name": "age",
                            "type": "number",
                            "required": false
                        }
                    ]
                }
            },
            {
                "operation": "update",
                "data": {
                    "id": "template-456",
                    "description": "Updated description"
                }
            },
            {
                "operation": "delete",
                "data": {
                    "id": "template-789",
                    "force": true
                }
            }
        ]
    }
    ```

    **Returns:**
    - `task_id`: Unique identifier for tracking the batch operation
    - `status`: "queued" indicating the task has been accepted
    - `message`: Confirmation message with operation count

    **Status Code:** 202 Accepted

    **Tracking Results:**
    - Use `GET /api/v1/queue/tasks/{task_id}` to check operation status
    - Use `GET /api/v1/queue/tasks/{task_id}/result` to get results once completed

    **Notes:**
    - Operations are executed in the order provided
    - If one operation fails, subsequent operations may still execute
    - Check the task result for individual operation outcomes
    - Template names cannot start with 'system_' prefix
    - Delete operations may fail if templates are in use (unless force=true)
    """
    # Import queue_client here to avoid circular dependencies
    from chaoscypher_core.constants import QUEUE_OPERATIONS
    from chaoscypher_core.queue import queue_client

    # Queue the bulk operation
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation="bulk_templates",
        data={"operations": [op.model_dump() for op in request.operations]},
        priority=settings.priorities.background,
        metadata={"operation_type": "bulk_templates"},
    )

    return BulkResponse(
        task_id=task_id,
        status="queued",
        message=f"Bulk templates operation queued with {len(request.operations)} operations",
    )
