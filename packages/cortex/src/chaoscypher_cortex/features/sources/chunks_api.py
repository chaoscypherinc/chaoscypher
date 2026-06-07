# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data access endpoints for Sources feature.

Read-only endpoints for accessing source data:
- Chunks: paginated chunk listing, single chunk retrieval
- Citations: entity attributions for a source
- Statistics: source-level stats
- Entities & Relationships: extracted graph data
- Templates: extraction templates
- LLM Metrics: token usage and call details
"""

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from chaoscypher_core.app_config import get_settings
from chaoscypher_cortex.features.sources.api import get_source_service
from chaoscypher_cortex.features.sources.models import (
    ChunkListResponse,
    ChunkResponse,
    SourceCitationListResponse,
    SourceEntitiesResponse,
    SourceLLMCallsResponse,
    SourceLLMMetricsResponse,
    SourceRelationshipsResponse,
    SourceStatsResponse,
    SourceTemplatesResponse,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,  # noqa: TC001 - FastAPI runtime dep
)
from chaoscypher_cortex.shared.api.errors import raise_if_not_found, resource_not_found_error
from chaoscypher_cortex.shared.api.models import PaginationMetadata
from chaoscypher_cortex.shared.api.responses import (
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    ErrorDetail,
)
from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,  # noqa: TC001 - FastAPI runtime dep
)


if TYPE_CHECKING:
    from chaoscypher_cortex.features.sources.service import SourceService

router = APIRouter()


# ================================
# Chunk Endpoints
# ================================


@router.get(
    "/{source_id}/chunks",
    response_model=ChunkListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source_chunks(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    pagination: PageParams,
    status: str | None = None,
) -> ChunkListResponse:
    """Get all chunks for a source.

    **Errors:**
    - 404: Source not found
    """
    raise_if_not_found(service.get_source(source_id), "Source not found")
    page, page_size = pagination
    raw = service.get_chunks(
        source_id=source_id,
        page=page,
        page_size=page_size,
        status=status,
    )
    total: int = raw.get("total", 0)
    total_pages = max(1, (total + page_size - 1) // page_size)
    return ChunkListResponse(
        data=[ChunkResponse(**c) for c in raw.get("chunks", [])],
        pagination=PaginationMetadata(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get(
    "/{source_id}/chunks/batch",
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
    summary="Fetch multiple small chunks by ID",
    description=(
        "Batch fetch — used by ChunkSourceDataPanel to display the raw "
        "chunk text for an extraction task's grouped small_chunk_ids. "
        "Returns ``{chunks: [{id, content, chunk_index}, ...]}`` ordered "
        "by ``chunk_index ASC``."
    ),
)
async def get_chunks_batch(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    ids: str = Query(..., description="Comma-separated chunk IDs."),
) -> dict[str, Any]:
    """Fetch multiple chunks by ID — supports the raw-vs-cleaned source-data panel."""
    raise_if_not_found(service.get_source(source_id), "Source not found")
    chunk_ids = [s.strip() for s in ids.split(",") if s.strip()]
    # Cap the batch size to the shared pagination ceiling so a giant ?ids=
    # list can't try to materialize an unbounded result set.
    max_ids = get_settings().pagination.max_page_size
    chunk_ids = chunk_ids[:max_ids]
    chunks = service.get_chunks_by_ids(chunk_ids=chunk_ids)
    return {"chunks": chunks}


@router.get(
    "/{source_id}/chunks/{chunk_id}",
    response_model=ChunkResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_chunk(
    _: CurrentUsername,
    source_id: str,
    chunk_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> dict[str, Any]:
    """Get a single chunk by ID."""
    chunk = raise_if_not_found(service.get_chunk(chunk_id), "Chunk not found")
    if chunk.get("source_id") != source_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(
                code="NOT_FOUND", message="Chunk not found for this source"
            ).model_dump(),
        )
    return chunk


# ================================
# Citation Endpoints
# ================================


@router.get(
    "/{source_id}/citations",
    response_model=SourceCitationListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_source_citations(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    pagination: PageParams,
) -> dict[str, Any]:
    """Get all citations (entity attributions) for a source."""
    page, page_size = pagination
    return service.get_citations(
        source_id=source_id,
        page=page,
        page_size=page_size,
    )


# ================================
# Statistics
# ================================


@router.get(
    "/{source_id}/stats",
    response_model=SourceStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source_stats(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> SourceStatsResponse:
    """Get statistics for a source."""
    stats = service.get_source_stats(source_id)
    return SourceStatsResponse(**raise_if_not_found(stats, "Source not found"))


# ================================
# Data Access (Entities, Relationships, Templates, LLM Metrics)
# ================================


@router.get(
    "/{source_id}/entities",
    response_model=SourceEntitiesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source_entities(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    pagination: PageParams,
    sort_by: str = Query(
        "default",
        description="Sort field: default, quality, confidence, name, type",
    ),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
) -> SourceEntitiesResponse:
    """Get paginated entities for a source.

    Returns entities extracted from the document with pagination support.
    Each entity includes a computed quality_score (0-100).

    **Errors:**
    - 404: Source not found
    """
    page, per_page = pagination
    try:
        return SourceEntitiesResponse(
            **service.get_entities(
                source_id=source_id,
                page=page,
                per_page=per_page,
                sort_by=sort_by,
                sort_order=sort_order,
            )
        )
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e


@router.get(
    "/{source_id}/relationships",
    response_model=SourceRelationshipsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source_relationships(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    pagination: PageParams,
) -> SourceRelationshipsResponse:
    """Get paginated relationships for a source.

    **Errors:**
    - 404: Source not found
    """
    page, per_page = pagination
    try:
        return SourceRelationshipsResponse(
            **service.get_relationships(
                source_id=source_id,
                page=page,
                per_page=per_page,
            )
        )
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e


@router.get(
    "/{source_id}/templates",
    response_model=SourceTemplatesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source_templates(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    pagination: PageParams,
    template_type: str | None = Query(None, description="Filter by type (node/edge)"),
) -> SourceTemplatesResponse:
    """Get paginated templates for a source.

    Returns templates created from extraction of this source document.

    **Query Parameters:**
    - template_type: Filter by 'node' or 'edge' (optional)
    - page: Page number (1-indexed, default: 1)
    - page_size: Items per page

    **Returns:**
    - templates: List of templates for the requested page
    - pagination: {page, per_page, total, total_pages, has_next, has_prev}

    **Errors:**
    - 404: Source not found
    """
    page, per_page = pagination
    try:
        return SourceTemplatesResponse(
            **service.get_source_templates(
                source_id=source_id,
                template_type=template_type,
                page=page,
                per_page=per_page,
            )
        )
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e


@router.get(
    "/{source_id}/llm_metrics",
    response_model=SourceLLMMetricsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_source_llm_metrics(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
) -> SourceLLMMetricsResponse:
    """Get LLM metrics summary for a source.

    **Errors:**
    - 404: Source not found
    """
    try:
        return SourceLLMMetricsResponse(**service.get_llm_metrics(source_id))
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e


@router.get(
    "/{source_id}/llm_metrics/calls",
    response_model=SourceLLMCallsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def list_source_llm_calls(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[SourceService, Depends(get_source_service)],
    pagination: PageParams,
    success: bool | None = Query(None, description="Filter by success status"),
) -> SourceLLMCallsResponse:
    """List individual LLM calls for a source with pagination.

    **Errors:**
    - 404: Source not found
    """
    page, per_page = pagination
    try:
        return SourceLLMCallsResponse(
            **service.list_llm_calls(
                source_id=source_id,
                page=page,
                per_page=per_page,
                success=success,
            )
        )
    except ValueError as e:
        raise resource_not_found_error("source", source_id) from e
