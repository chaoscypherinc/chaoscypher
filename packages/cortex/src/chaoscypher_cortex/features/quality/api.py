# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""API endpoints for Quality Scoring feature.

REST API for extraction quality evaluation:
- GET /quality/sources/{source_id} - Score single source
- GET /quality/sources/{source_id}/details - Score with breakdowns
- POST /quality/analyze - Batch analysis with filters
- GET /quality/domains - Domain performance comparison
- GET /quality/summary - Overall quality summary
- POST /quality/recalculate - Recalculate scores for all sources
- GET /quality/outdated - Get sources with outdated scores
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_cortex.features.quality.models import (
    DomainComparisonResponse,
    OutdatedSourcesResponse,
    QualityAnalysisPaginatedResponse,
    QualityAnalysisRequest,
    QualityAnalysisResponse,
    QualitySummaryResponse,
    RecalculateRequest,
    RecalculateResponse,
    SourceQualityDetailResponse,
    SourceQualityScoreResponse,
)
from chaoscypher_cortex.features.quality.service import QualityService
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
)
from chaoscypher_cortex.shared.api.errors import raise_if_not_found
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


# ================================
# Dependency Injection Factory
# ================================


def get_quality_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> QualityService:
    """Create QualityService with dependencies."""
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    return QualityService(adapter=adapter, database_name=settings.current_database)


# ================================
# Source Quality Endpoints
# ================================


@router.get(
    "/sources/{source_id}",
    response_model=SourceQualityScoreResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def score_source(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[QualityService, Depends(get_quality_service)],
    force_recalculate: bool = Query(
        default=False,
        description="Bypass cache and recalculate fresh scores",
    ),
) -> dict:
    """Score a single source's extraction quality.

    Returns quality metrics including:
    - Entity and relationship contributions (quality-weighted)
    - Average quality scores
    - Connectivity ratio
    - Low-quality item counts (inflation indicators)

    Uses cached scores when available for performance. Pass force_recalculate=true
    to bypass the cache and recalculate fresh scores.

    **Returns:**
    - Quality score breakdown for the source

    **Errors:**
    - 404: Source not found
    """
    result = service.score_source(
        source_id, include_details=False, force_recalculate=force_recalculate
    )
    return raise_if_not_found(result, f"Source {source_id} not found")


@router.get(
    "/sources/{source_id}/details",
    response_model=SourceQualityDetailResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def score_source_details(
    _: CurrentUsername,
    source_id: str,
    service: Annotated[QualityService, Depends(get_quality_service)],
    force_recalculate: bool = Query(
        default=False,
        description="Bypass cache and recalculate fresh scores",
    ),
) -> dict:
    """Score a source with detailed entity and relationship breakdowns.

    Returns full quality breakdown including individual scores for
    each entity and relationship.

    Note: Detail view always requires calculation as individual breakdowns
    are not cached.

    **Returns:**
    - Quality score breakdown with entity_scores and relationship_scores arrays

    **Errors:**
    - 404: Source not found
    """
    result = service.score_source(
        source_id, include_details=True, force_recalculate=force_recalculate
    )
    return raise_if_not_found(result, f"Source {source_id} not found")


# ================================
# Recalculation Endpoints
# ================================


@router.post(
    "/recalculate",
    response_model=RecalculateResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def recalculate_scores(
    _: CurrentUsername,
    request: RecalculateRequest,
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> dict:
    """Recalculate and cache quality scores for all sources.

    Use this endpoint after:
    - Updating scoring configuration (domain quality_scoring settings)
    - Upgrading to a new scoring algorithm version
    - Initial migration of existing data

    **Request Body:**
    - domain: Optional domain filter - only recalculate sources in this domain

    **Returns:**
    - Number of sources recalculated
    - List of any errors encountered
    """
    return service.recalculate_all_scores(domain=request.domain)


@router.get(
    "/outdated",
    response_model=OutdatedSourcesResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_outdated_sources(
    _: CurrentUsername,
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> dict:
    """Get sources with outdated or missing cached quality scores.

    Returns a list of sources that need score recalculation due to:
    - Missing cached scores (never calculated)
    - Outdated scoring version (algorithm changed since caching)

    **Returns:**
    - Count of outdated sources
    - List of source info for sources needing recalculation
    """
    outdated = service.get_outdated_sources()
    return {
        "outdated_count": len(outdated),
        "sources": outdated,
    }


# ================================
# Analysis Endpoints
# ================================


@router.post(
    "/analyze",
    response_model=QualityAnalysisResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def analyze_sources(
    _: CurrentUsername,
    request: QualityAnalysisRequest,
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> dict:
    """Analyze quality across multiple sources.

    **Request Body:**
    - source_ids: Specific source IDs to analyze (optional, None = all)
    - domain: Filter by extraction domain (optional)
    - min_entities: Minimum entity count to include (default: 0)

    **Returns:**
    - List of source quality scores
    - Aggregated average metrics
    """
    return service.analyze_sources(
        source_ids=request.source_ids,
        domain=request.domain,
        min_entities=request.min_entities,
    )


@router.get(
    "/analyze",
    response_model=QualityAnalysisPaginatedResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def analyze_sources_get(
    _: CurrentUsername,
    service: Annotated[QualityService, Depends(get_quality_service)],
    pagination: PageParams,
    domain: str | None = Query(default=None, description="Filter by extraction domain"),
    min_entities: int = Query(default=0, ge=0, description="Minimum entity count"),
    sort_by: str = Query(
        default="total_score",
        description="Sort field (total_score, avg_entity_quality, entity_count)",
    ),
    sort_order: str = Query(default="desc", description="Sort order (asc, desc)"),
) -> dict:
    """Analyze quality across sources with pagination.

    **Query Parameters:**
    - domain: Filter by extraction domain (optional)
    - min_entities: Minimum entity count to include (default: 0)
    - page: Page number (default: 1)
    - page_size: Items per page
    - sort_by: Sort field (default: total_score)
    - sort_order: Sort order (default: desc)

    **Returns:**
    - Paginated list of source quality scores
    - Pagination metadata
    - Aggregated average metrics
    """
    page, page_size = pagination
    result = service.analyze_sources(
        domain=domain,
        min_entities=min_entities,
    )

    sources = result["sources"]

    # Sort
    reverse = sort_order.lower() == "desc"
    if sort_by in ("total_score", "avg_entity_quality", "avg_relationship_quality", "entity_count"):
        sources.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)

    # Paginate
    total = len(sources)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    start = (page - 1) * page_size
    end = start + page_size
    page_sources = sources[start:end]

    return {
        "sources": page_sources,
        "total_sources": total,
        "avg_score": result["avg_score"],
        "avg_entity_quality": result["avg_entity_quality"],
        "avg_relationship_quality": result["avg_relationship_quality"],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }


# ================================
# Comparison Endpoints
# ================================


@router.get(
    "/domains",
    response_model=DomainComparisonResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def compare_domains(
    _: CurrentUsername,
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> dict:
    """Compare quality performance across extraction domains.

    Returns aggregated quality metrics for each domain to identify
    which domains produce the best extraction results.

    **Returns:**
    - List of domains with average scores, counts, and ratios
    - Sorted by average total score descending
    """
    return service.compare_domains()


@router.get(
    "/summary",
    response_model=QualitySummaryResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_quality_summary(
    _: CurrentUsername,
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> dict:
    """Get overall quality summary for the database.

    Provides a high-level overview of extraction quality including:
    - Total counts and averages
    - Top 5 and bottom 5 sources by score

    **Returns:**
    - Summary statistics
    - Top and bottom performing sources
    """
    return service.get_summary()
