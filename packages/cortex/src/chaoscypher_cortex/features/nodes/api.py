# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Nodes API Endpoints.

GET    /api/v1/nodes - List nodes
POST   /api/v1/nodes - Create node
GET    /api/v1/nodes/{id} - Get node
PATCH  /api/v1/nodes/{id} - Update node
DELETE /api/v1/nodes/{id} - Delete node
PATCH  /api/v1/nodes/{id}/position - Update position
POST   /api/v1/nodes/batch - Batch operations.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.models import (
    NodeCreate,
    NodeUpdate,
)
from chaoscypher_core.queue import queue_client
from chaoscypher_cortex.features.nodes.models import (
    CitationListResponse,
    ConnectionsResponse,
    NodePositionUpdateRequest,
    NodeResponse,
    PaginatedNodesResponse,
)
from chaoscypher_cortex.features.nodes.service import NodeService
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    RATE_LIMIT_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.kernel import BulkRequest, BulkResponse
from chaoscypher_cortex.shared.repositories.bundle import (
    RepositoryBundle,
    get_repositories,
)


router = APIRouter()


def get_node_service(
    repos: Annotated[RepositoryBundle, Depends(get_repositories)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> NodeService:
    """Get NodeService instance with RepositoryBundle.

    Repository classes are imported locally so the API module does not depend
    on feature-internal repository implementations. Only NodeService is a
    public surface area for the API layer.
    """
    # Local imports: repositories are an internal concern of NodeService.
    from chaoscypher_cortex.features.nodes.graph_repository import GraphNodeRepository
    from chaoscypher_cortex.features.nodes.sql_repository import SqlNodeRepository

    graph_node_repository = GraphNodeRepository(repos.graph)
    sql_node_repository = SqlNodeRepository(
        repos.session,
        repos.database_name,
        max_connected_edges=settings.batching.max_connected_edges,
    )

    return NodeService(
        graph_node_repository=graph_node_repository,
        sql_node_repository=sql_node_repository,
        graph_repository=repos.graph,
        search_repository=repos.search,
        settings=settings,
    )


@router.get(
    "",
    response_model=PaginatedNodesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_nodes(
    _: CurrentUsername,
    node_service: Annotated[NodeService, Depends(get_node_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    template_id: str | None = Query(None, description="Filter by template ID"),
    source_ids: list[str] | None = Query(None, description="Filter by source document IDs"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int | None = Query(None, ge=1, description="Items per page (default from settings)"),
    minimal: bool = Query(
        False, description="Load minimal fields only (better performance for large graphs)"
    ),
    include_stats: bool = Query(False, description="Include edge/citation stats for each node"),
) -> PaginatedNodesResponse:
    """List all nodes with pagination.

    **Query Parameters:**
    - `template_id`: Filter by template (optional)
    - `source_ids`: Filter by source document IDs (optional)
    - `page`: Page number (default: 1)
    - `page_size`: Items per page (uses settings.pagination.default_page_size if not provided)
    - `minimal`: If true, only loads essential fields (id, label, template_id, position).
                 Excludes properties and embedding for better performance with large graphs.
    - `include_stats`: If true, includes edge_count, citation_count, relationship_type_count
                       for each node. Slightly slower due to additional queries.

    **Returns:**
    - Paginated list of nodes with metadata

    **Note:** page_size is capped at settings.pagination.max_page_size
    """
    return node_service.list_nodes(
        template_id=template_id,
        source_ids=source_ids,
        page=page,
        page_size=page_size,
        minimal=minimal,
        include_stats=include_stats,
    )


@router.post(
    "",
    response_model=NodeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def create_node(
    _: CurrentUsername,
    node_create: NodeCreate,
    node_service: Annotated[NodeService, Depends(get_node_service)],
) -> NodeResponse:
    """Create a new node.

    **Request Body:**
    - `template_id`: Template ID (must exist)
    - `label`: Node label
    - `properties`: Node properties (optional)
    - `position`: Node position (optional)
    - `embedding`: Node embedding vector (optional)

    **Returns:**
    - Created node with generated ID

    **Side Effects:**
    - Automatically indexed for search

    **Errors:**
    - 404: Template not found
    """
    return await node_service.create_node(node_create)


@router.get(
    "/{node_id}",
    response_model=NodeResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_node(
    _: CurrentUsername,
    node_id: str,
    node_service: Annotated[NodeService, Depends(get_node_service)],
) -> NodeResponse:
    """Get a specific node by ID.

    **Errors:**
    - 404: Node not found
    """
    return node_service.get_node(node_id=node_id)


@router.get(
    "/{node_id}/connections",
    response_model=ConnectionsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_node_connections(
    _: CurrentUsername,
    node_id: str,
    node_service: Annotated[NodeService, Depends(get_node_service)],
    sort_by: str = Query("edge_count", description="Sort by: edge_count, label, relationship"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int | None = Query(None, ge=1, description="Items per page (default from settings)"),
) -> ConnectionsResponse:
    """Get connected nodes for a given node.

    Returns nodes that are directly connected to this node (via edges),
    with their total edge counts for sorting by importance.

    **Query Parameters:**
    - `sort_by`: Sort field (edge_count, label, relationship). Default: edge_count
    - `page`: Page number (default: 1)
    - `page_size`: Items per page (uses settings.pagination.default_page_size if not provided)

    **Returns:**
    - List of connected nodes with:
      - Node ID, label, template
      - Total edge count (importance indicator)
      - Relationship type connecting to parent
      - Direction (incoming/outgoing)

    **Errors:**
    - 404: Node not found
    """
    return node_service.get_node_connections(
        node_id=node_id,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{node_id}/citations",
    response_model=CitationListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_node_citations(
    _: CurrentUsername,
    node_id: str,
    node_service: Annotated[NodeService, Depends(get_node_service)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int | None = Query(None, ge=1, description="Items per page (default from settings)"),
) -> CitationListResponse:
    """Get all citations (source attributions) for a node.

    Returns information about where this entity was extracted from,
    including source document details and text chunks with context.

    **Use Case:**
    - See which documents mention this entity
    - Verify extraction sources
    - Trace entity provenance

    **Query Parameters:**
    - `page`: Page number (default: 1)
    - `page_size`: Items per page (uses settings.pagination.default_page_size if not provided)

    **Returns:**
    - List of citations with:
      - Source document metadata (title, type, URL)
      - Chunk content (text snippet where entity was found)
      - Location metadata (page number, section)
      - Confidence score and extraction method

    **Note:**
    - Citations are capped at 100 items per page due to chunk content size
    - Overall limit respects settings.pagination.max_page_size

    **Errors:**
    - 404: Node not found
    """
    return node_service.get_node_citations(
        node_id=node_id,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/{node_id}",
    response_model=NodeResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_node(
    _: CurrentUsername,
    node_id: str,
    node_update: NodeUpdate,
    node_service: Annotated[NodeService, Depends(get_node_service)],
) -> NodeResponse:
    """Update an existing node.

    **Request Body:**
    - `label`: New label (optional)
    - `properties`: New properties (optional)
    - `position`: New position (optional)
    - `embedding`: New embedding (optional)

    **Side Effects:**
    - Search index automatically updated

    **Errors:**
    - 404: Node not found
    """
    return await node_service.update_node(node_id, node_update)


@router.patch(
    "/{node_id}/position",
    response_model=NodeResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_node_position(
    _: CurrentUsername,
    node_id: str,
    position_update: NodePositionUpdateRequest,
    node_service: Annotated[NodeService, Depends(get_node_service)],
) -> NodeResponse:
    """Update only the position of a node (optimized for Save Layout feature).

    **Performance Optimization:**
    - Doesn't trigger event publishing (avoids overwhelming system during layout adjustments)
    - Still updates search index

    **Request Body:**
    - `position`: { "x": float, "y": float }

    **Errors:**
    - 404: Node not found
    """
    return node_service.update_node_position(node_id, position_update)


@router.delete(
    "/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_node(
    _: CurrentUsername,
    node_id: str,
    node_service: Annotated[NodeService, Depends(get_node_service)],
) -> None:
    """Delete a node.

    **Side Effects:**
    - Removed from search index automatically

    **Errors:**
    - 404: Node not found
    """
    node_service.delete_node(node_id)


@router.post(
    "/batch",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BulkResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **RATE_LIMIT_RESPONSE,
    },
)
async def batch_nodes_operation(
    _: CurrentUsername,
    request: BulkRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BulkResponse:
    """Queue batch operations on nodes.

    This endpoint accepts a list of operations (create, update, delete) to be performed
    on nodes in a single batch. The operations are queued for background processing.

    **Request Body:**
    - `operations`: List of operations, each with:
      - `operation`: Operation type ('create', 'update', or 'delete')
      - `data`: Operation-specific data (node properties for create/update, node_id for delete)

    **Example Request:**
    ```json
    {
        "operations": [
            {
                "operation": "create",
                "data": {
                    "template_id": "template-123",
                    "label": "New Node",
                    "properties": {"key": "value"}
                }
            },
            {
                "operation": "update",
                "data": {
                    "id": "node-456",
                    "label": "Updated Label"
                }
            },
            {
                "operation": "delete",
                "data": {"id": "node-789"}
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

    **Side Effects:**
    - Created/updated nodes are automatically indexed for search
    - Deleted nodes are removed from search index

    **Notes:**
    - Operations are executed in the order provided
    - If one operation fails, subsequent operations may still execute
    - Check the task result for individual operation outcomes
    """
    # Queue the bulk operation
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation="bulk_nodes",
        data={"operations": [op.model_dump() for op in request.operations]},
        priority=settings.priorities.background,
        metadata={"operation_type": "bulk_nodes"},
    )

    return BulkResponse(
        task_id=task_id,
        status="queued",
        message=f"Bulk nodes operation queued with {len(request.operations)} operations",
    )
