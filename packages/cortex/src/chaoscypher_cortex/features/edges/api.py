# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edges API Endpoints.

GET    /api/v1/edges - List edges
POST   /api/v1/edges - Create edge
GET    /api/v1/edges/{id} - Get edge
PATCH  /api/v1/edges/{id} - Update edge
DELETE /api/v1/edges/{id} - Delete edge (204)
POST   /api/v1/edges/batch - Batch operations.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.models import (
    EdgeCreate,
    EdgeUpdate,
)
from chaoscypher_core.queue import queue_client
from chaoscypher_core.repo_factories import get_graph_repository
from chaoscypher_core.services.graph.management.edge import EdgeService
from chaoscypher_cortex.features.edges.models import (
    EdgeResponse,
    PaginatedEdgesResponse,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    RATE_LIMIT_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.database import get_current_session
from chaoscypher_cortex.shared.kernel import BulkRequest, BulkResponse


# Create router
router = APIRouter()


# Dependency to get edge service
def get_edge_service(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EdgeService:
    """Get EdgeService instance (uses engine service directly)."""
    graph_repository = get_graph_repository(session, settings.current_database)
    return EdgeService(graph_repository=graph_repository)


# ============================================================================
# Edge CRUD Endpoints
# ============================================================================


@router.get(
    "",
    response_model=PaginatedEdgesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_edges(
    _: CurrentUsername,
    edge_service: Annotated[EdgeService, Depends(get_edge_service)],
    pagination: PageParams,
    source_node_id: str | None = Query(None, description="Filter by source node ID"),
    source_ids: list[str] | None = Query(None, description="Filter by source document IDs"),
    minimal: bool = Query(
        False, description="Load minimal fields only (better performance for large graphs)"
    ),
) -> PaginatedEdgesResponse:
    """List all edges with pagination.

    **Query Parameters:**
    - `source_node_id`: Filter edges by source node (optional)
    - `source_ids`: Filter by source document IDs (optional)
    - `page`: Page number (default: 1)
    - `page_size`: Items per page
    - `minimal`: If true, only loads essential fields (id, source_node_id, target_node_id, label, template_id).
                 Excludes properties for better performance with large graphs.

    **Returns:**
    - Paginated list of edges with metadata
    """
    page, page_size = pagination
    result = edge_service.list_edges(
        source_node_id=source_node_id,
        source_ids=source_ids,
        page=page,
        page_size=page_size,
        minimal=minimal,
    )
    # Convert dict results to Pydantic models
    return PaginatedEdgesResponse(
        data=[EdgeResponse(**edge_dict) for edge_dict in result["data"]],
        pagination=result["pagination"],
    )


@router.post(
    "",
    response_model=EdgeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def create_edge(
    _: CurrentUsername,
    edge_create: EdgeCreate,
    edge_service: Annotated[EdgeService, Depends(get_edge_service)],
) -> EdgeResponse:
    """Create a new edge between two nodes.

    **Request Body:**
    - `template_id`: Edge template ID
    - `source_node_id`: Source node ID
    - `target_node_id`: Target node ID
    - `label`: Edge label
    - `properties`: Edge properties (optional)

    **Returns:**
    - Created edge with generated ID and timestamps
    """
    edge_dict = edge_service.create_edge(edge_create)
    return EdgeResponse(**edge_dict)


@router.get(
    "/{edge_id}",
    response_model=EdgeResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_edge(
    _: CurrentUsername,
    edge_id: str,
    edge_service: Annotated[EdgeService, Depends(get_edge_service)],
) -> EdgeResponse:
    """Get a specific edge by ID.

    **Path Parameters:**
    - `edge_id`: Edge ID

    **Returns:**
    - Edge details

    **Errors:**
    - 404: Edge not found
    """
    edge_dict = edge_service.get_edge(edge_id)
    return EdgeResponse(**edge_dict)


@router.patch(
    "/{edge_id}",
    response_model=EdgeResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_edge(
    _: CurrentUsername,
    edge_id: str,
    edge_update: EdgeUpdate,
    edge_service: Annotated[EdgeService, Depends(get_edge_service)],
) -> EdgeResponse:
    """Update an existing edge.

    **Path Parameters:**
    - `edge_id`: Edge ID

    **Request Body:**
    - `label`: New label (optional)
    - `properties`: New properties (optional)

    **Returns:**
    - Updated edge

    **Errors:**
    - 404: Edge not found
    """
    edge_dict = edge_service.update_edge(edge_id, edge_update)
    return EdgeResponse(**edge_dict)


@router.delete(
    "/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_edge(
    _: CurrentUsername,
    edge_id: str,
    edge_service: Annotated[EdgeService, Depends(get_edge_service)],
) -> None:
    """Delete an edge.

    **Path Parameters:**
    - `edge_id`: Edge ID

    **Returns:**
    - 204 No Content on success

    **Errors:**
    - 404: Edge not found
    """
    edge_service.delete_edge(edge_id)


@router.post(
    "/batch",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **RATE_LIMIT_RESPONSE,
    },
)
async def batch_edges_operation(
    _: CurrentUsername,
    request: BulkRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BulkResponse:
    """Queue batch operations on edges.

    This endpoint accepts a list of operations (create, update, delete) to be performed
    on edges in a single batch. The operations are queued for background processing.

    **Request Body:**
    - `operations`: List of operations, each with:
      - `operation`: Operation type ('create', 'update', or 'delete')
      - `data`: Operation-specific data (edge properties for create/update, edge_id for delete)

    **Example Request:**
    ```json
    {
        "operations": [
            {
                "operation": "create",
                "data": {
                    "template_id": "edge-template-123",
                    "source_node_id": "node-123",
                    "target_node_id": "node-456",
                    "label": "relates_to",
                    "properties": {"strength": 0.8}
                }
            },
            {
                "operation": "update",
                "data": {
                    "id": "edge-789",
                    "label": "depends_on"
                }
            },
            {
                "operation": "delete",
                "data": {"id": "edge-012"}
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
    - Source and target nodes must exist for create operations
    """
    # Queue the bulk operation
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation="bulk_edges",
        data={"operations": [op.model_dump() for op in request.operations]},
        priority=settings.priorities.background,
        metadata={"operation_type": "bulk_edges"},
    )

    return BulkResponse(
        task_id=task_id,
        status="queued",
        message=f"Bulk edges operation queued with {len(request.operations)} operations",
    )
