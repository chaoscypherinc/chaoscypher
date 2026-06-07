# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Grounding API for MCP Integration.

Read-only graph query endpoints for external AI agents.

This module provides the API router and factory function for the grounding
feature. The business logic lives in ``grounding_service.py``.

Architecture:
- Service Layer: GroundingService (in grounding_service.py) handles business logic
- Repository Pattern: Uses GraphRepository for RDF access
- Dependency Injection: FastAPI Depends() for clean separation
- Read-Only: All endpoints are GET operations only
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.graph.grounding_service import GroundingService
from chaoscypher_cortex.features.graph.models import (
    GroundingEdgeListResponse,
    GroundingNodeListResponse,
    NeighborsResponse,
    NodeWithEdgesResponse,
)
from chaoscypher_cortex.shared.api.dependencies import (
    LimitParam,
    PageParams,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.database import get_current_session


# Create router
router = APIRouter()


# ============================================================================
# Dependency Injection
# ============================================================================


def get_grounding_service(
    session: Annotated[SafeSession, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GroundingService:
    """Get GroundingService instance.

    FastAPI dependency that instantiates GroundingService with proper
    repository injection. Ensures clean separation of concerns.
    """
    graph_repository = GraphRepository(session, settings.current_database)
    return GroundingService(graph_repository, settings)


# ============================================================================
# API Endpoints
# ============================================================================


@router.get(
    "/nodes",
    response_model=GroundingNodeListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_nodes(
    _: CurrentUsername,
    grounding_service: Annotated[GroundingService, Depends(get_grounding_service)],
    pagination: PageParams,
    q: str | None = Query(None, description="Search query (filters by label or properties)"),
    template_id: str | None = Query(None, description="Filter by template ID"),
) -> GroundingNodeListResponse:
    """Search and list nodes in the knowledge graph.

    **MCP Use Case:**
    AI agents can discover relevant knowledge nodes by searching and filtering.
    This is the primary entry point for knowledge discovery.

    **Query Parameters:**
    - `q`: Text search across node labels and properties (optional, post-filter)
    - `template_id`: Filter nodes by template/entity type (optional)
    - `page`: 1-based page number (default: 1)
    - `page_size`: Items per page (defaults to settings, capped at max)

    **Returns:**
    Paginated nodes envelope:
    - `data`: list of node objects
    - `pagination`: `{total, page, page_size, total_pages, has_next, has_prev}`

    **Example Use Cases:**
    - Find all Person nodes: `?template_id=person`
    - Search for "Einstein": `?q=Einstein`
    - Page through results: `?page=2&page_size=50`

    **Response Format:**
    ```json
    {
        "data": [
            {
                "id": "node_abc123",
                "template_id": "person",
                "label": "Albert Einstein",
                "properties": {"birth_year": 1879, "field": "Physics"},
                "position": {"x": 100, "y": 200},
                "embedding": [0.1, 0.2],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        ],
        "pagination": {
            "total": 1,
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
            "has_next": false,
            "has_prev": false
        }
    }
    ```

    **Note on `q`:** the text filter is applied in Python *after* the SQL page
    is fetched, so `pagination.total` reflects the SQL-filtered count
    (`template_id` only). Iterate pages until `has_next` is false to discover
    every match.
    """
    page, page_size = pagination
    return grounding_service.search_nodes(
        q=q, template_id=template_id, page=page, page_size=page_size
    )


@router.get(
    "/nodes/{node_id}",
    response_model=NodeWithEdgesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_node(
    node_id: str,
    _: CurrentUsername,
    grounding_service: Annotated[GroundingService, Depends(get_grounding_service)],
) -> NodeWithEdgesResponse:
    """Get a single node with all connected edges.

    **MCP Use Case:**
    AI agents can retrieve complete context for a specific node, including
    all its relationships (both incoming and outgoing edges). This provides
    full local graph context for reasoning.

    **Path Parameters:**
    - `node_id`: Node ID to retrieve

    **Returns:**
    Node details with edge lists:
    - `node`: Full node object with all properties
    - `outgoing_edges`: Edges where this node is the source
    - `incoming_edges`: Edges where this node is the target
    - `total_outgoing`: Count of outgoing edges
    - `total_incoming`: Count of incoming edges

    **Example Use Cases:**
    - Get complete node context: `/nodes/node_abc123`
    - Understand all relationships for a concept
    - Build local subgraph around a node

    **Response Format:**
    ```json
    {
        "node": {
            "id": "node_abc123",
            "label": "Albert Einstein",
            ...
        },
        "outgoing_edges": [
            {
                "id": "edge_xyz789",
                "source_node_id": "node_abc123",
                "target_node_id": "node_def456",
                "label": "worked_on",
                "properties": {"year": 1905}
            }
        ],
        "incoming_edges": [...],
        "total_outgoing": 5,
        "total_incoming": 12
    }
    ```

    **Errors:**
    - `404`: Node not found
    """
    return grounding_service.get_node_with_edges(node_id)


@router.get(
    "/edges",
    response_model=GroundingEdgeListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_edges(
    _: CurrentUsername,
    grounding_service: Annotated[GroundingService, Depends(get_grounding_service)],
    pagination: PageParams,
    source_node_id: str | None = Query(None, description="Filter by source node ID"),
    target_node_id: str | None = Query(None, description="Filter by target node ID"),
) -> GroundingEdgeListResponse:
    """Search and list edges (relationships) in the knowledge graph.

    **MCP Use Case:**
    AI agents can discover and explore relationships between nodes.
    This is useful for understanding connection patterns and graph structure.

    **Query Parameters:**
    - `source_node_id`: Filter edges by source node (optional)
    - `target_node_id`: Filter edges by target node (optional)
    - `page`: 1-based page number (default: 1)
    - `page_size`: Items per page (defaults to settings, capped at max)

    **Returns:**
    Paginated edges envelope:
    - `data`: list of edge objects
    - `pagination`: `{total, page, page_size, total_pages, has_next, has_prev}`

    **Example Use Cases:**
    - Find all edges from a node: `?source_node_id=node_abc123`
    - Find all edges to a node: `?target_node_id=node_def456`
    - Page through results: `?page=2&page_size=50`

    **Response Format:**
    ```json
    {
        "data": [
            {
                "id": "edge_xyz789",
                "template_id": "relationship",
                "source_node_id": "node_abc123",
                "target_node_id": "node_def456",
                "label": "worked_on",
                "properties": {"year": 1905},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        ],
        "pagination": {
            "total": 1,
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
            "has_next": false,
            "has_prev": false
        }
    }
    ```
    """
    page, page_size = pagination
    return grounding_service.search_edges(
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/nodes/{node_id}/neighbors",
    response_model=NeighborsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_neighbors(
    node_id: str,
    _: CurrentUsername,
    grounding_service: Annotated[GroundingService, Depends(get_grounding_service)],
    limit: LimitParam,
    direction: str = Query(
        "both",
        description="Direction to follow edges: 'outgoing', 'incoming', or 'both'",
    ),
) -> NeighborsResponse:
    """Get nodes connected to this node via edges.

    **MCP Use Case:**
    AI agents can traverse the knowledge graph by following relationships.
    This enables graph exploration, path finding, and relationship discovery.

    **Path Parameters:**
    - `node_id`: Node ID to find neighbors for

    **Query Parameters:**
    - `direction`: Edge direction to follow
      - `outgoing`: Follow edges where this node is the source
      - `incoming`: Follow edges where this node is the target
      - `both`: Follow edges in both directions (default)
    - `limit`: Maximum neighbors (1-1000, default: 100)

    **Returns:**
    Neighbor list with relationship context:
    - `node_id`: Source node ID
    - `neighbors`: List of connected nodes with relationship info
    - `total`: Total neighbors returned
    - `direction`: Direction filter applied

    **Example Use Cases:**
    - Find what a node relates to: `?direction=outgoing`
    - Find what relates to a node: `?direction=incoming`
    - Find all connections: `?direction=both`
    - Limit traversal: `?limit=10`

    **Response Format:**
    ```json
    {
        "node_id": "node_abc123",
        "neighbors": [
            {
                "node": {
                    "id": "node_def456",
                    "label": "Theory of Relativity",
                    ...
                },
                "relationship_type": "worked_on",
                "edge_id": "edge_xyz789",
                "direction": "outgoing",
                "edge_properties": {"year": 1905}
            }
        ],
        "total": 1,
        "direction": "both"
    }
    ```

    **Errors:**
    - `404`: Node not found
    - `400`: Invalid direction parameter
    """
    return grounding_service.get_node_neighbors(node_id=node_id, direction=direction, limit=limit)
