# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search API Endpoints.

GET  /api/v1/search - Unified search (keyword, semantic, hybrid)
GET  /api/v1/search/stats - Index stats
GET  /api/v1/search/indexes/status - Index rebuild status
POST /api/v1/search/indexes - Rebuild search indexes (auto-detects regeneration need)
POST /api/v1/search/embeddings - Generate embeddings for nodes.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.repo_factories import get_search_repository
from chaoscypher_cortex.features.search.models import (
    GenerateEmbeddingsResponse,
    IndexStatusResponse,
    QueuedRebuildResponse,
    RebuildIndexResponse,
    SearchResponse,
    SearchStatistics,
)
from chaoscypher_cortex.features.search.service import SearchService
from chaoscypher_cortex.shared.api.dependencies import (
    LimitParam,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    SERVICE_UNAVAILABLE_RESPONSE,
    ErrorDetail,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.repositories.bundle import (
    RepositoryBundle,
    get_repositories,
)


router = APIRouter()


def get_search_service(
    repos: Annotated[RepositoryBundle, Depends(get_repositories)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SearchService:
    """Create SearchService with RepositoryBundle."""
    return SearchService(
        search_repository=repos.search,
        graph_repository=repos.graph,
        indexing_repository=repos.adapter,
        source_repository=repos.adapter,
        sources_repository=repos.adapter,
        settings=settings,
    )


@router.get(
    "",
    response_model=SearchResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def search(
    _: CurrentUsername,
    search_service: Annotated[SearchService, Depends(get_search_service)],
    limit: LimitParam,
    q: str = Query(..., description="Search query string"),
    search_type: Literal["keyword", "semantic", "hybrid"] = Query(
        "keyword", description="Search type"
    ),
) -> SearchResponse:
    """Unified search endpoint supporting multiple search strategies.

    **Query Parameters:**
    - `q`: Search query string (required)
    - `search_type`: Search type - keyword (full-text), semantic (vector), or hybrid (semantic with fallback)
    - `limit`: Maximum results

    **Search Types:**
    - **keyword**: Full-text keyword search
    - **semantic**: Vector similarity search using embeddings
    - **hybrid**: Semantic search with keyword fallback if no results

    **Returns:**
    - List of search results with nodes and relevance scores
    """
    try:
        return await search_service.search(q, limit=limit, search_type=search_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code="VALIDATION_FAILED", message=str(exc)).model_dump(),
        ) from exc


@router.get(
    "/stats",
    response_model=SearchStatistics,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_search_stats(
    _: CurrentUsername,
    search_service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchStatistics:
    """Get search index stats.

    **Returns:**
    - Fulltext index document count
    - Vector index size
    - Vector dimension
    """
    return search_service.get_stats()


@router.get(
    "/indexes/status",
    response_model=IndexStatusResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_index_status(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings)],
) -> IndexStatusResponse:
    """Check if search indexes need rebuilding.

    Returns the current state of the search index including
    whether a full reindex is needed (model/dimension change detected).
    """
    search_repo = get_search_repository(database_name=settings.current_database)
    stats = search_repo.get_index_stats()
    return IndexStatusResponse(
        needs_rebuild=search_repo.needs_full_reindex,
        embedding_model=search_repo.embedding_model,
        vector_dimensions=search_repo.vector_dim,
        **stats,
    )


@router.post("/indexes")
async def rebuild_search_indexes(
    _: CurrentUsername,
    response: Response,
    search_service: Annotated[SearchService, Depends(get_search_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RebuildIndexResponse | QueuedRebuildResponse:
    """Rebuild search indexes.

    Auto-detects whether embeddings need regeneration:
    - If model/dimensions changed: queues full regeneration (ops queue), returns 202
    - Otherwise: fast rebuild from stored embeddings (inline), returns 200

    **Use Cases:**
    - After bulk imports
    - When index is corrupted
    - After changing embedding model or dimensions
    """
    search_repo = get_search_repository(database_name=settings.current_database)

    if search_repo.needs_full_reindex:
        from chaoscypher_core.operations.queue_utils import (
            queue_rebuild_search_indexes,
        )

        task_id = await queue_rebuild_search_indexes(
            database_name=settings.current_database,
            regenerate=True,
            priority=settings.priorities.background,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return QueuedRebuildResponse(task_id=task_id)

    # Fast path: rebuild from stored embeddings (no queue needed)
    result = search_service.rebuild_indexes()

    # Invalidate search repo cache so next request uses fresh index
    from chaoscypher_core.repo_factories.search_factory import (
        invalidate_search_repository,
    )

    invalidate_search_repository()
    return result


@router.post(
    "/embeddings",
    response_model=GenerateEmbeddingsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def generate_embeddings(
    _: CurrentUsername,
    search_service: Annotated[SearchService, Depends(get_search_service)],
) -> GenerateEmbeddingsResponse:
    """Generate embeddings for all nodes that don't have them.

    **RESTful Design:**
    - POST /embeddings creates embeddings for nodes
    - This is a resource creation operation for embedding resources

    **Process:**
    1. Find all nodes without embeddings
    2. Trigger embedding generation workflow for each node
    3. Queue them in the LLM service (background processing)

    **Use Cases:**
    - Existing nodes created before auto-embedding was enabled
    - Nodes imported from external sources without embeddings

    **Note:**
    - Processing happens asynchronously in the background
    - Use GET /api/v1/search/stats to monitor progress

    **Returns:**
    - Total nodes in graph
    - Number of nodes queued for embedding generation
    - Success status
    """
    # Note: trigger_service is not yet available in VSA
    # This will be wired up when triggers feature is enhanced
    return await search_service.generate_embeddings(trigger_service=None)
