# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export statistics DTOs.

Pure Pydantic models describing the cached statistics a CCX 3.0 export
routes into the ``chaoscypher.statistics`` named graph. Stats are NOT part
of the neutral CCX standard — they are app-cached, recomputable opinion.

Zero backend dependencies — works in both backend and CLI.
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ============================================================================
# Statistics Models
# ============================================================================


class DateRange(BaseModel):
    """Temporal range of data based on created_at timestamps."""

    earliest: datetime | None = Field(None, description="Earliest timestamp in dataset")
    latest: datetime | None = Field(None, description="Latest timestamp in dataset")


class EmbeddingStats(BaseModel):
    """Statistics about embeddings in the graph."""

    is_present: bool = Field(
        ...,
        description="Whether embeddings were present in the source data (regardless of whether vectors are included in this export)",
    )
    vectors_included: bool = Field(
        ..., description="Whether embedding vectors are included in this export"
    )
    node_count: int = Field(0, description="Number of nodes with embeddings")
    dimensions: int | None = Field(None, description="Vector dimensions if present")
    model_name: str | None = Field(None, description="Model used to generate embeddings")

    # Coverage metrics
    total_embeddable_items: int = Field(0, description="Total nodes that should have embeddings")
    coverage_pct: float = Field(
        0.0, description="Percentage of items with embeddings (0.0 to 100.0)"
    )

    # Model details
    model_version: str | None = Field(None, description="Model version (e.g., 'v1.5')")
    model_provider: str | None = Field(
        None, description="Provider: 'ollama', 'openai', 'anthropic', etc."
    )

    model_config = {"protected_namespaces": ()}


class ChunkingConfig(BaseModel):
    """Chunking strategy and parameters used to create chunks.

    Stored in manifest for reproducibility - users can understand exactly how
    the data was chunked when evaluating RAG compatibility.
    """

    # Strategy identification
    strategy: str = Field(
        ...,
        description="Chunking strategy: 'recursive_character', 'semantic', 'sentence', 'fixed_size'",
    )

    # Core parameters
    target_chunk_size: int = Field(..., description="Target chunk size in characters")
    chunk_overlap: int = Field(..., description="Overlap between chunks in characters")
    min_chunk_size: int = Field(..., description="Minimum chunk size threshold")
    max_chunk_size: int = Field(..., description="Maximum chunk size threshold")

    # Boundary handling
    respect_boundaries: bool = Field(
        ..., description="Whether chunking respects sentence/paragraph boundaries"
    )
    separators: list[str] = Field(
        default_factory=list, description="Separator priority used (e.g., ['. ', '! ', '? '])"
    )

    # Hierarchical grouping (for extraction)
    group_size: int = Field(..., description="Chunks per extraction group")
    group_overlap: int = Field(..., description="Overlap between extraction groups")
    auto_group_size: bool = Field(
        ..., description="Whether group size was auto-calculated based on LLM context"
    )

    # Text normalization
    normalize_newlines: bool = Field(
        ..., description="Whether single newlines were converted to spaces"
    )


class TemplateStats(BaseModel):
    """Statistics about templates in templates.jsonld."""

    avg_properties_per_template: float = Field(0.0, description="Average properties per template")
    most_complex_template: str | None = Field(None, description="Template with most properties")


class KnowledgeStats(BaseModel):
    """Statistics about knowledge graph in knowledge.jsonld."""

    node_count: int = Field(0, description="Total nodes")
    edge_count: int = Field(0, description="Total edges")
    relationship_types: dict[str, int] = Field(
        default_factory=dict, description="Count of edges per relationship type/label"
    )
    avg_degree: float = Field(0.0, description="Average connections per node")
    max_degree: int = Field(0, description="Maximum connections on any single node")
    graph_density: float = Field(0.0, description="Graph density (0.0 to 1.0)")
    isolated_node_count: int = Field(0, description="Count of nodes with zero connections")
    date_range: DateRange = Field(
        default_factory=lambda: DateRange(earliest=None, latest=None),
        description="Temporal span of data",
    )
    embeddings: EmbeddingStats = Field(
        default_factory=lambda: EmbeddingStats(
            is_present=False,
            vectors_included=False,
            node_count=0,
            dimensions=None,
            model_name=None,
            total_embeddable_items=0,
            coverage_pct=0.0,
            model_version=None,
            model_provider=None,
        ),
        description="Embedding statistics",
    )


class LensStats(BaseModel):
    """Statistics about lenses in lenses.jsonld."""

    total_count: int = Field(0, description="Total number of lenses")
    input_templates: dict[str, int] = Field(
        default_factory=dict, description="Count of lenses by input template ID"
    )
    output_templates: dict[str, int] = Field(
        default_factory=dict, description="Count of lenses by output template ID"
    )
    has_transformation_rules: int = Field(
        0, description="Number of lenses with transformation rules defined"
    )


class WorkflowStats(BaseModel):
    """Statistics about workflows in workflows.jsonld."""

    total_workflows: int = Field(0, description="Total number of workflows")
    enabled_workflows: int = Field(0, description="Number of enabled workflows")
    disabled_workflows: int = Field(0, description="Number of disabled workflows")
    total_steps: int = Field(0, description="Total workflow steps across all workflows")
    avg_steps_per_workflow: float = Field(0.0, description="Average steps per workflow")
    trigger_count: int = Field(0, description="Number of event triggers")
    tools_used: dict[str, int] = Field(
        default_factory=dict,
        description="Count of workflow steps by tool type (e.g., core:query, ai:summarize)",
    )
    tools_count: int = Field(0, description="Number of unique tools used across all workflow steps")


class SourceStats(BaseModel):
    """Statistics about sources in sources.jsonl."""

    # Basic counts
    active_sources: int = Field(0, description="Number of active sources")
    archived_sources: int = Field(0, description="Number of archived sources")
    total_chunks: int = Field(0, description="Total chunks across all sources")
    total_citations: int = Field(0, description="Total citations across all sources")
    source_types: dict[str, int] = Field(
        default_factory=dict, description="Count of sources by type (pdf, text, csv, webpage, etc.)"
    )
    domains: dict[str, int] = Field(
        default_factory=dict,
        description="Count of sources by extraction domain (literary, scientific, ...) — "
        "lets the hub show a package category without parsing the package contents",
    )
    avg_chunks_per_source: float = Field(0.0, description="Average chunks per source")
    total_content_length: int = Field(0, description="Total content length across all sources")
    date_range: DateRange = Field(
        default_factory=lambda: DateRange(earliest=None, latest=None),
        description="Temporal span of sources",
    )

    # Chunk quality metrics
    avg_chunk_length: int = Field(0, description="Average characters per chunk")
    min_chunk_length: int = Field(0, description="Minimum chunk length in characters")
    max_chunk_length: int = Field(0, description="Maximum chunk length in characters")
    chunk_length_std_dev: float = Field(0.0, description="Standard deviation of chunk lengths")
    empty_chunk_count: int = Field(0, description="Number of chunks with no content")

    # Embedding coverage
    chunks_with_embeddings: int = Field(0, description="Number of chunks that have embeddings")
    embedding_coverage_pct: float = Field(
        0.0, description="Percentage of chunks with embeddings (0.0 to 100.0)"
    )
    embedding_models_used: list[str] = Field(
        default_factory=list, description="Unique embedding models used across chunks"
    )
    vectors_included: bool = Field(
        ..., description="Whether embedding vectors are included in this export"
    )

    # RAG readiness
    rag_ready: bool = Field(
        False, description="Whether all chunks have content and embeddings (ready for RAG)"
    )
    indexing_complete_pct: float = Field(
        0.0, description="Percentage of sources fully indexed (0.0 to 100.0)"
    )

    # Citation quality metrics
    avg_citations_per_source: float = Field(0.0, description="Average citations per source")
    avg_citation_confidence: float = Field(
        0.0, description="Average citation confidence score (0.0 to 1.0)"
    )
    unique_entities_cited: int = Field(0, description="Number of unique entities referenced")
    extraction_methods: dict[str, int] = Field(
        default_factory=dict, description="Count of citations by extraction method"
    )

    # Chunking configuration
    chunking_config: ChunkingConfig | None = Field(
        None, description="Chunking strategy and parameters used to create chunks"
    )


__all__ = [
    "ChunkingConfig",
    "DateRange",
    "EmbeddingStats",
    "KnowledgeStats",
    "LensStats",
    "SourceStats",
    "TemplateStats",
    "WorkflowStats",
]
