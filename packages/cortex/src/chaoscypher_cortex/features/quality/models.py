# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic models for Quality Scoring feature.

Defines request and response models for the quality API endpoints.
"""

from pydantic import BaseModel, Field


__all__ = [
    "DomainComparisonResponse",
    "DomainPerformanceResponse",
    "EntityQualityScoreResponse",
    "OutdatedSourceResponse",
    "OutdatedSourcesResponse",
    "PaginationInfo",
    "QualityAnalysisPaginatedResponse",
    "QualityAnalysisRequest",
    "QualityAnalysisResponse",
    "QualitySummaryResponse",
    "RecalculateRequest",
    "RecalculateResponse",
    "RelationshipQualityScoreResponse",
    "SourceQualityDetailResponse",
    "SourceQualityScoreResponse",
]


class EntityQualityScoreResponse(BaseModel):
    """Quality score breakdown for a single entity."""

    entity_name: str = Field(description="Name of the entity")
    entity_type: str = Field(description="Type of the entity")
    description_score: float = Field(description="Score for description richness (0-20)")
    confidence_score: float = Field(description="Score for extraction confidence (0-15)")
    cross_chunk_score: float = Field(description="Score for cross-chunk mentions (0-15)")
    properties_score: float = Field(description="Score for property richness (0-15)")
    aliases_score: float = Field(description="Score for alias count (0-10)")
    type_value_score: float = Field(description="Score based on entity type tier (0-25)")
    total_score: float = Field(description="Sum of all component scores (0-100)")


class RelationshipQualityScoreResponse(BaseModel):
    """Quality score breakdown for a single relationship."""

    relationship_type: str = Field(description="Type of the relationship")
    source_entity: str = Field(description="Name of source entity")
    target_entity: str = Field(description="Name of target entity")
    justification_score: float = Field(description="Score for justification richness (0-35)")
    confidence_score: float = Field(description="Score for extraction confidence (0-25)")
    specificity_score: float = Field(description="Score based on relationship type tier (0-25)")
    valid_refs_score: float = Field(description="Score for valid entity references (0-15)")
    total_score: float = Field(description="Sum of all component scores (0-100)")


class SourceQualityScoreResponse(BaseModel):
    """Quality score breakdown for an entire source."""

    source_id: str = Field(description="ID of the source")
    source_title: str | None = Field(default=None, description="Title of the source")
    domain: str | None = Field(default=None, description="Extraction domain used")
    entity_count: int = Field(description="Number of entities")
    relationship_count: int = Field(description="Number of relationships")
    entity_contribution: float = Field(description="Sum of quality-weighted entity scores")
    relationship_contribution: float = Field(
        description="Sum of quality-weighted relationship scores"
    )
    connectivity_bonus: float = Field(description="Bonus for connected entities")
    total_score: float = Field(description="Richness score (unbounded, quantity-driven)")
    avg_entity_quality: float = Field(description="Average quality per entity (0-100)")
    avg_relationship_quality: float = Field(description="Average quality per relationship (0-100)")
    connectivity_ratio: float = Field(description="Ratio of connected entities (0-1)")
    quality_grade: float = Field(
        default=0.0, description="Quality rating 0-100 (independent of volume)"
    )
    quality_label: str = Field(
        default="Low", description="Quality label: Outstanding/Excellent/Good/Fair/Low"
    )
    low_quality_entity_count: int = Field(
        default=0, description="Entities with score < 40 (inflation indicator)"
    )
    low_quality_relationship_count: int = Field(
        default=0, description="Relationships with score < 40"
    )
    density_ratio: float = Field(default=0.0, description="Relationships per entity ratio")
    density_score: float = Field(default=0.0, description="Density score normalized to 0-100")
    topology_score: float = Field(
        default=0.0, description="Combined connectivity + density score (0-100)"
    )
    pollution_penalty: float = Field(
        default=0.0, description="Penalty for low-quality items (0-15)"
    )
    structural_penalty: float = Field(
        default=0.0,
        description="Penalty for graph-shape noise signals hub-skew + reciprocal-rate (0-15)",
    )
    hub_skew: float = Field(
        default=1.0,
        description="max_entity_degree / median_entity_degree (≥1.0; high = one entity over-connected)",
    )
    reciprocal_rate: float = Field(
        default=0.0,
        description="Fraction of edges with a same-type reciprocal partner (0-1)",
    )
    coverage_score: float = Field(default=0.0, description="Entities per chunk normalized to 0-100")


class SourceQualityDetailResponse(SourceQualityScoreResponse):
    """Detailed quality score including individual breakdowns."""

    entity_scores: list[EntityQualityScoreResponse] = Field(
        default_factory=list,
        description="Individual entity score breakdowns",
    )
    relationship_scores: list[RelationshipQualityScoreResponse] = Field(
        default_factory=list,
        description="Individual relationship score breakdowns",
    )


class QualityAnalysisRequest(BaseModel):
    """Request for batch quality analysis."""

    source_ids: list[str] | None = Field(
        default=None, description="Specific source IDs to analyze (None = all)"
    )
    domain: str | None = Field(default=None, description="Filter by extraction domain")
    min_entities: int = Field(default=0, description="Minimum entity count to include")


class QualityAnalysisResponse(BaseModel):
    """Response from batch quality analysis."""

    sources: list[SourceQualityScoreResponse] = Field(description="Quality scores for each source")
    total_sources: int = Field(description="Total sources analyzed")
    avg_score: float = Field(description="Average total score across sources")
    avg_entity_quality: float = Field(description="Average entity quality")
    avg_relationship_quality: float = Field(description="Average relationship quality")


class PaginationInfo(BaseModel):
    """Pagination metadata."""

    page: int = Field(description="Current page number")
    page_size: int = Field(description="Items per page")
    total: int = Field(description="Total items")
    total_pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there is a next page")
    has_prev: bool = Field(description="Whether there is a previous page")


class QualityAnalysisPaginatedResponse(BaseModel):
    """Paginated response from quality analysis."""

    sources: list[SourceQualityScoreResponse] = Field(
        description="Quality scores for the current page"
    )
    total_sources: int = Field(description="Total sources analyzed")
    avg_score: float = Field(description="Average total score across all sources")
    avg_entity_quality: float = Field(description="Average entity quality across all sources")
    avg_relationship_quality: float = Field(description="Average relationship quality")
    pagination: PaginationInfo = Field(description="Pagination metadata")


class DomainPerformanceResponse(BaseModel):
    """Quality performance for a single domain."""

    domain: str = Field(description="Domain name")
    source_count: int = Field(description="Number of sources in this domain")
    avg_total_score: float = Field(description="Average total score")
    avg_entity_quality: float = Field(description="Average entity quality")
    avg_relationship_quality: float = Field(description="Average relationship quality")
    avg_connectivity_ratio: float = Field(description="Average connectivity ratio")
    total_entities: int = Field(description="Total entities across all sources")
    total_relationships: int = Field(description="Total relationships across all sources")


class DomainComparisonResponse(BaseModel):
    """Comparison of quality across domains."""

    domains: list[DomainPerformanceResponse] = Field(description="Performance metrics per domain")


class QualitySummaryResponse(BaseModel):
    """Overall quality summary for the database."""

    total_sources: int = Field(description="Total sources with extractions")
    total_entities: int = Field(description="Total entities extracted")
    total_relationships: int = Field(description="Total relationships extracted")
    avg_total_score: float = Field(description="Average total score")
    avg_entity_quality: float = Field(description="Average entity quality")
    avg_relationship_quality: float = Field(description="Average relationship quality")
    avg_quality_grade: float = Field(description="Average quality grade (0-100)")
    avg_connectivity_ratio: float = Field(description="Average connectivity ratio")
    top_sources: list[SourceQualityScoreResponse] = Field(
        description="Top 5 sources by total score"
    )
    bottom_sources: list[SourceQualityScoreResponse] = Field(
        description="Bottom 5 sources by total score"
    )


class RecalculateRequest(BaseModel):
    """Request for batch score recalculation."""

    domain: str | None = Field(
        default=None,
        description="Optional domain filter - only recalculate sources in this domain",
    )


class RecalculateResponse(BaseModel):
    """Response from batch score recalculation."""

    recalculated_count: int = Field(description="Number of sources recalculated")
    errors: list[dict] = Field(
        default_factory=list,
        description="List of errors encountered during recalculation",
    )


class OutdatedSourceResponse(BaseModel):
    """Info about a source with outdated scores."""

    id: str = Field(description="Source ID")
    title: str | None = Field(default=None, description="Source title")
    cached_scores_version: int | None = Field(default=None, description="Version of cached scores")
    current_version: int = Field(description="Current scoring algorithm version")


class OutdatedSourcesResponse(BaseModel):
    """Response listing sources with outdated scores."""

    outdated_count: int = Field(description="Number of sources with outdated scores")
    sources: list[OutdatedSourceResponse] = Field(
        description="List of sources needing recalculation"
    )
