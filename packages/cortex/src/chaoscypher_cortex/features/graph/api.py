# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph API Endpoints.

GET  /api/v1/graph/canvas - Bulk fetch all graph data for canvas rendering.
POST /api/v1/graph/cleanup - Remove corrupt nodes from graph.
GET  /api/v1/graph/sourcegroups - Get image source groups for graph visualization.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.repo_factories import get_graph_repository
from chaoscypher_cortex.features.graph.models import (
    CanvasResponse,
    SourceGroupListResponse,
    SourceGroupResponse,
)
from chaoscypher_cortex.features.graph.service import GraphService
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    QueuedResetResponse,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.database import get_current_session


# Create router
router = APIRouter()


# Dependency to get graph service
def get_graph_service(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GraphService:
    """Get GraphService instance."""
    graph_repository = get_graph_repository(session, settings.current_database)
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    return GraphService(
        graph_repository,
        adapter=adapter,
        database_name=settings.current_database,
        settings=settings,
    )


# ============================================================================
# Graph Operations
# ============================================================================


@router.get(
    "/canvas",
    response_model=CanvasResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_canvas_data(
    _: CurrentUsername,
    graph_service: Annotated[GraphService, Depends(get_graph_service)],
    source_ids: Annotated[list[str] | None, Query()] = None,
) -> CanvasResponse:
    """Bulk fetch all graph data for canvas rendering in a single request.

    Returns minimal node, edge, and template data optimized for the graph
    canvas. Eliminates the need for separate paginated API calls (40+
    HTTP round-trips reduced to 1).

    The underlying ``GraphService.get_canvas_data`` is synchronous and
    can materialise a large JSON payload (capped by
    ``settings.pagination.canvas_max_{nodes,edges}``). We dispatch it
    onto a worker thread so the FastAPI event loop is not blocked
    during the SQLAlchemy fetch + Pydantic serialisation — every other
    /api/ request would otherwise stall for the duration.

    Query Parameters:
        source_ids: Optional list of source document IDs to filter by.

    Returns:
        Dict with nodes, edges, and templates arrays.
    """
    data = await asyncio.to_thread(graph_service.get_canvas_data, source_ids=source_ids)
    return CanvasResponse(**data)


@router.get(
    "/source_groups",
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_source_groups(
    _: CurrentUsername,
    graph_service: Annotated[GraphService, Depends(get_graph_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SourceGroupListResponse:
    """Get image source groups for graph visualization.

    Returns image-type sources with their extracted entity node IDs,
    used by the graph canvas to create virtual source group nodes.
    Only includes committed image sources that have extracted entities.
    Each group includes the extraction domain icon for visual display.
    """
    groups = await graph_service.get_source_groups()

    # Build domain name → icon lookup from the domain registry
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.services.sources.engine.extraction.domains import (
        get_domain_registry,
    )

    registry = get_domain_registry(
        build_engine_settings(settings), database_name=settings.current_database
    )
    domain_icons = {d["name"]: d.get("icon") for d in registry.list_domain_info()}

    # Enrich each group with its domain icon
    for g in groups:
        domain = g.get("extraction_domain")
        if domain:
            g["extraction_domain_icon"] = domain_icons.get(domain)

    return SourceGroupListResponse(groups=[SourceGroupResponse(**g) for g in groups])


@router.post(
    "/cleanup",
    response_model=QueuedResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def cleanup_corrupt_nodes(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueuedResetResponse:
    """Queue a corrupt-node cleanup pass on the graph.

    Enqueues a background task. Corrupt nodes are those missing required
    predicates (nodeId, templateId, or label). Previously ran inline on
    the API thread; now runs on the worker. Poll
    ``GET /queue/tasks/{task_id}/result`` for cleanup stats.

    **Use Case:**
    - Remove nodes that show as "None" in the UI
    - Clean up after data import failures
    - Fix graph integrity issues

    **Returns:**
    - ``task_id`` — poll for {nodes_removed, edges_removed}.
    """
    from chaoscypher_core.constants import OP_GRAPH_CLEANUP
    from chaoscypher_core.operations.queue_utils import queue_graph_cleanup

    task_id = await queue_graph_cleanup(
        database_name=settings.current_database,
        priority=settings.priorities.background,
    )
    return QueuedResetResponse(task_id=task_id, operation_type=OP_GRAPH_CLEANUP)
