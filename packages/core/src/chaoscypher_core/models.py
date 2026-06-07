# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Core models for chaoscypher-engine.

Pure Pydantic models (no SQLModel coupling) for use across the engine.
These models define the data structures for nodes, edges, templates,
suggestions, and other entities.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class UserPrincipal:
    """Normalized user principal for ACL checks.

    Accepts dict, object, or None at service boundaries; service code
    only ever sees this dataclass.
    """

    id: int | None
    is_admin: bool


# ============================================================================
# Type Aliases
# ============================================================================

SearchMode = Literal["hybrid", "semantic", "keyword"]
"""Search mode for hybrid, semantic-only, or keyword-only search."""

AnalysisDepth = Literal["quick", "full"]
"""Extraction depth — ``'full'`` processes all chunks, ``'quick'`` samples."""

ProgressStage = Literal["chunking", "indexing", "extraction"]
"""Pipeline stage name passed to progress callbacks."""

# Forward reference resolved after model definitions below.
# See ``ProgressCallback`` at the bottom of this file.


# ============================================================================
# Enums
# ============================================================================


class SourceStatus(StrEnum):
    """Source processing lifecycle statuses.

    Normal lifecycle for text-only sources::

        PENDING → INDEXING → INDEXED → EXTRACTING → EXTRACTED → COMMITTING → COMMITTED

    For image-bearing sources with the vision pipeline enabled::

        PENDING → INDEXING → VISION_PENDING → INDEXING (resume) → INDEXED → EXTRACTING → …

    ``VISION_PENDING`` is set by the indexing handler after it has enqueued
    per-page vision tasks and returns without blocking.  The vision finalizer
    transitions the source back to ``INDEXING`` (CAS from ``VISION_PENDING``)
    and enqueues an ``OP_INDEX_DOCUMENT`` task with ``resume_after_vision=True``
    so the resume run merges descriptions into the chunks and reaches
    ``INDEXED``.  The recovery scanner uses ``VISION_PENDING`` as its gate
    for the vision-recovery branch.
    """

    PENDING = "pending"
    INDEXING = "indexing"
    VISION_PENDING = "vision_pending"
    INDEXED = "indexed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXTRACTING = "extracting"
    MCP_EXTRACTING = "mcp_extracting"
    EXTRACTED = "extracted"
    COMMITTING = "committing"
    COMMITTED = "committed"
    ERROR = "error"


class SourceErrorStage(StrEnum):
    """Stage at which a source's pipeline failed.

    Single source of truth for the ``error_stage`` column on SourceRow.
    Values match the strings written by ``_apply_failure``,
    ``mark_source_exhausted``, and ``fail_url_fetch`` today, so this
    enum requires no data migration.

    The Cortex retry endpoint compares ``error_stage`` to these values
    to decide which lifecycle status to reset to. The Cortex abort path
    translates the in-flight ``SourceStatus`` (e.g. EXTRACTING) to the
    matching ``SourceErrorStage`` (e.g. EXTRACTION) before persisting.
    """

    INDEXING = "indexing"
    EXTRACTION = "extraction"
    COMMIT = "commit"
    URL_FETCH = "url_fetch"
    RECOVERY_EXHAUSTED = "recovery_exhausted"


class StepToolType(StrEnum):
    """Type of tool used in a workflow step."""

    SYSTEM_TOOL = "system_tool"
    USER_TOOL = "user_tool"
    WORKFLOW = "workflow"


class PropertyType(StrEnum):
    """Types of properties that can be attached to nodes/edges."""

    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    URL = "url"
    EMAIL = "email"
    ENUM = "enum"
    JSON = "json"
    NODE_REFERENCE = "node_reference"
    NODE_REFERENCE_LIST = "node_reference_list"


# ============================================================================
# Property Models
# ============================================================================


class PropertyDefinition(BaseModel):
    """Definition of a property type in a template."""

    name: str = Field(..., description="Property name (unique within template)")
    display_name: str = Field(..., description="Human-readable display name")
    property_type: PropertyType = Field(..., description="Data type of the property")
    required: bool = Field(default=False, description="Whether this property is required")
    default_value: Any | None = Field(default=None, description="Default value if not provided")
    enum_values: list[str] | None = Field(default=None, description="Allowed values for enum type")
    description: str | None = Field(default=None, description="Description of the property")
    validation_pattern: str | None = Field(default=None, description="Regex pattern for validation")
    allowed_node_types: list[str] | None = Field(
        default=None, description="For NODE_REFERENCE types: allowed template IDs"
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Graph Models
# ============================================================================


class NodePosition(BaseModel):
    """Position of a node in graph canvas (optional, for UI)."""

    x: float = Field(..., description="X coordinate")
    y: float = Field(..., description="Y coordinate")

    model_config = ConfigDict(extra="forbid")


class Node(BaseModel):
    """Graph node (entity) - complete version for engine use.

    Includes all fields needed by GraphRepository including timestamps.
    """

    id: str = Field(..., description="Unique node identifier")
    label: str = Field(..., description="Human-readable label/title")
    template_id: str = Field(..., description="Template type this node follows")
    entity_type: str | None = Field(
        default=None,
        description="Extracted entity type (e.g., 'Person'). NULL on legacy nodes.",
    )
    properties: dict[str, Any] = Field(default_factory=dict, description="Property values")
    position: NodePosition | None = Field(default=None, description="Position in graph canvas")
    source_id: str | None = Field(
        default=None, description="Source document this node was extracted from"
    )
    embedding: list[float] | None = Field(
        default=None, description="Vector embedding for similarity search"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Last update timestamp"
    )

    model_config = ConfigDict(extra="forbid")


class NodeCreate(BaseModel):
    """Node creation data.

    Used when creating new nodes in the graph via GraphRepository.create_node()
    """

    template_id: str = Field(..., description="Template type for this node")
    label: str = Field(..., description="Human-readable label/title")
    entity_type: str | None = Field(
        default=None,
        description=(
            "Extracted entity type (e.g., 'Person'). Populated by the commit "
            "path from the extraction entity dict. Nullable for callers that "
            "don't have a type to record."
        ),
    )
    properties: dict[str, Any] = Field(default_factory=dict, description="Initial property values")
    position: NodePosition | None = Field(
        default=None, description="Optional position in graph canvas"
    )
    embedding: list[float] | None = Field(default=None, description="Optional embedding vector")
    source_id: str | None = Field(default=None, description="Source ID for enabled filtering")

    model_config = ConfigDict(extra="forbid")


class NodeUpdate(BaseModel):
    """Node update data.

    Used when updating existing nodes via GraphRepository.update_node()
    All fields are optional - only provided fields will be updated.
    """

    label: str | None = Field(default=None, description="New label")
    properties: dict[str, Any] | None = Field(
        default=None, description="New properties (full replacement)"
    )
    position: NodePosition | None = Field(default=None, description="New position")
    embedding: list[float] | None = Field(default=None, description="New embedding vector")

    model_config = ConfigDict(extra="forbid")


class Edge(BaseModel):
    """Graph edge (relationship) - complete version for engine use.

    Includes all fields needed by GraphRepository including timestamps.
    """

    id: str = Field(..., description="Unique edge identifier")
    template_id: str = Field(..., description="Relationship type template")
    source_node_id: str = Field(..., description="Source node ID (from)")
    target_node_id: str = Field(..., description="Target node ID (to)")
    label: str = Field(..., description="Human-readable relationship label")
    properties: dict[str, Any] = Field(default_factory=dict, description="Relationship properties")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Last update timestamp"
    )

    model_config = ConfigDict(extra="forbid")


class EdgeWithNodes(Edge):
    """Edge with hydrated source and target node objects.

    Returned by ``GraphRepository.list_edges(with_nodes=True)`` to eliminate
    the O(N) ``get_node()``-in-a-loop antipattern.  Both endpoint nodes are
    batch-loaded in a single IN-query and attached here so callers can access
    ``edge.source_node.label`` without issuing additional round trips.

    Example::

        edges = graph_repo.list_edges(with_nodes=True)
        for edge in edges:
            print(f"{edge.source_node.label} → {edge.target_node.label}")
    """

    source_node: Node | None = Field(
        default=None,
        description="Hydrated source node (set when list_edges(with_nodes=True))",
    )
    target_node: Node | None = Field(
        default=None,
        description="Hydrated target node (set when list_edges(with_nodes=True))",
    )

    model_config = ConfigDict(extra="forbid")


class EdgeCreate(BaseModel):
    """Edge creation data.

    Used when creating new edges via GraphRepository.create_edge()
    """

    template_id: str = Field(..., description="Relationship type template")
    source_node_id: str = Field(..., description="Source node ID (from)")
    target_node_id: str = Field(..., description="Target node ID (to)")
    label: str = Field(..., description="Human-readable relationship label")
    properties: dict[str, Any] = Field(
        default_factory=dict, description="Initial relationship properties"
    )
    source_id: str | None = Field(default=None, description="Source ID for enabled filtering")

    model_config = ConfigDict(extra="forbid")


class EdgeUpdate(BaseModel):
    """Edge update data.

    Used when updating existing edges via GraphRepository.update_edge()
    All fields are optional - only provided fields will be updated.
    """

    label: str | None = Field(default=None, description="New label")
    properties: dict[str, Any] | None = Field(
        default=None, description="New properties (full replacement)"
    )

    model_config = ConfigDict(extra="forbid")


class Template(BaseModel):
    """Node or edge template definition - complete version for engine use.

    Includes property definitions, system flag, and timestamps.
    """

    id: str = Field(..., description="Unique template identifier")
    name: str = Field(..., description="Template name")
    template_type: str = Field(..., description="Type: 'node' or 'edge'")
    description: str | None = Field(default=None, description="Template description")
    properties: list[PropertyDefinition] = Field(
        default_factory=list, description="Property definitions"
    )
    is_system: bool = Field(default=False, description="Whether this is a system template")
    color: str | None = Field(default=None, description="Display color in graph canvas")
    icon: str | None = Field(default=None, description="Display icon in graph canvas")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Last update timestamp"
    )
    source_id: str | None = Field(default=None, description="Source ID for enabled filtering")

    model_config = ConfigDict(extra="forbid")


class TemplateCreate(BaseModel):
    """Template creation data.

    Used when creating new templates via GraphRepository.create_template()
    """

    name: str = Field(..., description="Template name")
    template_type: str = Field(..., description="Type: 'node' or 'edge'")
    description: str | None = Field(default=None, description="Template description")
    properties: list[PropertyDefinition] = Field(
        default_factory=list, description="Property definitions"
    )
    icon: str | None = Field(default=None, description="MUI icon name for visual identity")
    color: str | None = Field(default=None, description="Hex color for visual identity")
    source_id: str | None = Field(default=None, description="Source ID for enabled filtering")

    model_config = ConfigDict(extra="forbid")


class TemplateUpdate(BaseModel):
    """Template update data.

    Used when updating existing templates via GraphRepository.update_template()
    All fields are optional - only provided fields will be updated.
    """

    name: str | None = Field(default=None, description="New template name")
    description: str | None = Field(default=None, description="New description")
    properties: list[PropertyDefinition] | None = Field(
        default=None, description="New property definitions"
    )
    icon: str | None = Field(default=None, description="MUI icon name for visual identity")
    color: str | None = Field(default=None, description="Hex color for visual identity")
    embedding: list[float] | None = Field(
        default=None, description="Embedding vector for semantic search"
    )
    embedding_model: str | None = Field(
        default=None, description="Model used to generate embedding"
    )
    embedding_dimensions: int | None = Field(
        default=None, description="Dimensionality of embedding vector"
    )

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Database Models
# ============================================================================


class PaginatedResult(BaseModel):
    """Paginated result from Engine list operations.

    Wraps a list of domain models with pagination metadata.
    Items in ``data`` are typed domain models (Node, Edge, or Template),
    not raw dicts.

    Example:
        result = engine.list_nodes(page=1, page_size=20)
        for node in result.data:
            print(node.label)
        print(f"Page {result.page} of {result.total_pages}")
    """

    data: list[Any] = Field(description="List of domain model items")
    total: int = Field(description="Total number of items across all pages")
    page: int = Field(description="Current page number (1-based)")
    page_size: int = Field(description="Number of items per page")
    total_pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether a next page exists")
    has_prev: bool = Field(description="Whether a previous page exists")


class DatabaseInfo(BaseModel):
    """Database metadata.

    Returned by DatabaseProtocol.get_database() and used across core, cortex,
    and CLI packages. Contains the union of fields needed by all consumers.
    """

    name: str = Field(..., description="Database name")
    path: str = Field(
        ..., description="Full path to database directory (e.g., /data/databases/default)"
    )
    description: str | None = Field(default=None, description="Database description")
    created_at: datetime | None = Field(default=None, description="When database was created")
    exists: bool = Field(default=True, description="Whether the database file exists on disk")
    size: int = Field(default=0, description="Database file size in bytes")
    last_modified: datetime | None = Field(
        default=None, description="When the database file was last modified"
    )

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_path(
        cls,
        name: str,
        path: str,
        app_db_filename: str = "app.db",
    ) -> DatabaseInfo:
        """Create DatabaseInfo from a database directory path.

        Inspects the database file on disk to populate ``exists``, ``size``,
        and ``last_modified``.

        Args:
            name: Database name.
            path: Path to the database directory.
            app_db_filename: Database filename inside the directory.

        """
        db_file = os.path.join(path, app_db_filename)
        exists = os.path.exists(db_file)
        size = os.path.getsize(db_file) if exists else 0
        last_modified = None
        if exists:
            timestamp = os.path.getmtime(db_file)
            last_modified = datetime.fromtimestamp(timestamp, tz=UTC)

        return cls(
            name=name,
            path=path,
            exists=exists,
            size=size,
            last_modified=last_modified,
        )


class Source(BaseModel):
    """Unified source document model - from upload through committed.

    Single model representing a document throughout its entire lifecycle:
    upload → indexing → extraction → commit to graph.
    """

    id: str = Field(..., description="Source identifier (stable from upload)")
    database_name: str = Field(..., description="Database this source belongs to")

    # File metadata
    filename: str = Field(..., description="Original filename")
    filepath: str | None = Field(default=None, description="Storage path")
    file_type: str | None = Field(default=None, description="MIME type")
    file_size: int | None = Field(default=None, description="File size in bytes")

    # Source metadata
    title: str | None = Field(default=None, description="Document title")
    source_type: str | None = Field(default=None, description="Source type (pdf, docx, etc)")
    origin_url: str | None = Field(default=None, description="Original URL if applicable")

    # Lifecycle status
    status: str = Field(default="pending", description="Current lifecycle status")
    enabled: bool = Field(default=True, description="Whether source is enabled for search/graph")
    error_message: str | None = Field(default=None, description="Error message if status=error")

    # Indexing stage
    chunk_count: int = Field(default=0, description="Number of chunks created")
    embedding_model: str | None = Field(default=None, description="Model used for embeddings")
    indexing_completed_at: datetime | None = Field(
        default=None, description="When indexing completed"
    )

    # Extraction stage
    extraction_depth: str | None = Field(default=None, description="Extraction depth (quick/full)")
    extraction_completed_at: datetime | None = Field(
        default=None, description="When extraction completed"
    )
    extraction_entities_count: int = Field(default=0, description="Number of entities extracted")
    extraction_relationships_count: int = Field(
        default=0, description="Number of relationships extracted"
    )

    # Commit stage
    commit_completed_at: datetime | None = Field(default=None, description="When commit completed")
    commit_nodes_created: int = Field(default=0, description="Nodes created in graph")
    commit_edges_created: int = Field(default=0, description="Edges created in graph")

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Upload timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Last update timestamp"
    )

    # User metadata
    user_metadata: dict[str, Any] | None = Field(default=None, description="Custom user metadata")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Engine Convenience Models
# ============================================================================


class DatabaseStats(BaseModel):
    """Database statistics returned by Engine.get_stats()."""

    database_name: str = Field(description="Name of the database")
    data_dir: str = Field(description="Path to the database directory")
    nodes: int = Field(description="Total number of nodes")
    edges: int = Field(description="Total number of edges")
    templates: int = Field(description="Total number of templates")

    model_config = ConfigDict(extra="forbid")


class IndexingResult(BaseModel):
    """Result from Engine.index_source()."""

    chunks_count: int = Field(description="Number of chunks indexed")
    embedding_model: str = Field(description="Embedding model used")
    embedding_dimensions: int = Field(description="Embedding vector dimensions")

    model_config = ConfigDict(extra="forbid")


class RebuildResult(BaseModel):
    """Result from Engine.rebuild_indexes()."""

    total_nodes: int = Field(description="Total nodes reindexed")
    nodes_with_embeddings: int = Field(description="Nodes with embedding vectors")
    chunks_indexed: int = Field(description="Document chunk embeddings indexed")

    model_config = ConfigDict(extra="forbid")


class ChunksResult(BaseModel):
    """Result from ChunkingService.create_chunks().

    Contains the actual chunks and groups produced by the chunking service,
    plus summary counts. Returned by ``ChunkingService.create_chunks()`` and
    accepted by ``ChunkingService.store_chunks()``.
    """

    small_chunks: list[dict[str, Any]] = Field(description="Small RAG chunks for indexing")
    hierarchical_groups: list[dict[str, Any]] = Field(
        description="Grouped chunks for entity extraction"
    )
    total_small_chunks: int = Field(description="Number of small RAG chunks (after filtering)")
    total_groups: int = Field(description="Number of hierarchical groups (after filtering)")
    total_original_chunks: int = Field(description="Total small chunks before depth filtering")
    total_original_groups: int = Field(description="Total groups before depth filtering")
    chunks_filtered: int = Field(
        default=0,
        description=(
            "Number of merge events recorded by the chunker — sub-threshold "
            "chunks coalesced into a neighbor instead of being emitted as "
            "their own row. Workstream 5.3 (2026-05-07): exposed so the "
            "indexing handler can increment ``QualityCounter.CHUNKS_COALESCED``. "
            "W5 follow-up (2026-05-08): the underlying chunker now coalesces "
            "rather than dropping, so this field counts merges, not lost "
            "content. Phase 7 (2026-05-09): DB column renamed to "
            "``chunks_coalesced_count`` (Alembic 0029)."
        ),
    )
    normalize_drops: int = Field(
        default=0,
        description=(
            "Total regex-substitution count from ``_normalize_text``: "
            "page-header removals (step 1) + broken-sentence joins (step 2). "
            "P2T10 (2026-05-08): surfaced so the indexing handler can "
            "increment ``QualityCounter.CHUNKER_NORMALIZE_DROPS``. Zero when "
            "``normalize_newlines`` is disabled."
        ),
    )
    prestrip_lines_removed: int = Field(
        default=0,
        description=(
            "Number of lines removed by ``_prestrip_structural_noise`` across "
            "all passes (page numbers, structural markers, TOC blocks, repeated "
            "headers/footers). P2T10 (2026-05-08): surfaced so the indexing "
            "handler can increment "
            "``QualityCounter.CHUNKER_PRESTRIP_LINES_REMOVED``. Zero when "
            "``normalize_remove_structural_noise`` is disabled."
        ),
    )
    chunks_skipped_by_depth: int = Field(
        default=0,
        description=(
            "Number of hierarchical groups silently dropped by the quick-mode "
            "cap (``max(0, len(all_groups) - 5)``). Zero for ``full`` depth. "
            "P2T10 (2026-05-08): surfaced so the indexing handler can "
            "increment ``QualityCounter.CHUNKS_SKIPPED_BY_DEPTH``."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class ChunkingResult(BaseModel):
    """Result from Engine.chunk_document().

    Contains metadata about the chunking operation including source ID
    and chunk counts. Use the source_id to pass to subsequent pipeline
    stages like ``engine.index_source()`` or ``engine.commit()``.
    """

    source_id: str = Field(description="Identifier for the chunked source")
    total_small_chunks: int = Field(description="Number of small RAG chunks created")
    total_groups: int = Field(description="Number of hierarchical groups formed")
    analysis_depth: str = Field(description="Analysis depth used: 'full' or 'quick'")

    model_config = ConfigDict(extra="forbid")


class ExtractionResult(BaseModel):
    """Result from ChunkingService.process() standalone extraction.

    Contains extracted entities, relationships, and domain detection metadata.
    Use ``model_dump_json()`` for JSON output or attribute access for fields.

    Example:
        >>> result = await ChunkingService().process(text)
        >>> print(result.domain, result.domain_confidence)
        >>> print(result.model_dump_json(indent=2))

    """

    entities: list[dict[str, Any]] = Field(description="Extracted entities")
    relationships: list[dict[str, Any]] = Field(description="Extracted relationships")
    cached_embeddings: list[Any] = Field(
        default_factory=list, description="Cached entity embeddings from deduplication"
    )
    chunk_ids: list[list[str]] = Field(
        default_factory=list, description="Small chunk IDs per hierarchical group"
    )
    domain: str = Field(default="generic", description="Detected document domain")
    domain_confidence: float = Field(default=0.0, description="Domain detection confidence score")
    filtering_log: dict[str, Any] | None = Field(
        default=None, description="Entity filtering log (only present when entities were removed)"
    )

    model_config = ConfigDict(extra="forbid")


class ProcessingResult(BaseModel):
    """Result from Engine.process_document() and Engine.add_document()."""

    source_id: str = Field(description="Identifier of the processed source document")
    nodes: list[str] = Field(default_factory=list, description="IDs of created nodes")
    edges: list[str] = Field(default_factory=list, description="IDs of created edges")
    templates: list[str] = Field(default_factory=list, description="IDs of created templates")
    status: str | None = Field(
        default=None,
        description=(
            "Terminal pipeline status. ``None`` for the normal "
            "extract-and-commit path; ``awaiting_confirmation`` when the "
            "domain-confirmation gate parked the source before extraction."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class EngineSearchResult(BaseModel):
    """Individual search result from Engine.search()."""

    label: str = Field(description="Node label or chunk content preview")
    score: float = Field(description="Relevance score")
    result_type: str = Field(description="Result type: 'node' or 'chunk'")
    id: str = Field(description="Node ID or chunk ID")
    template_id: str | None = Field(default=None, description="Template ID (nodes only)")
    source: str | None = Field(default=None, description="Source filename (chunks only)")
    content: str | None = Field(default=None, description="Chunk content preview (chunks only)")

    @property
    def snippet(self) -> str:
        """Best text preview regardless of result type.

        Returns content for chunks (with label fallback), label for nodes.
        """
        if self.result_type == "chunk" and self.content:
            return self.content
        return self.label

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# LLM Response Models
# ============================================================================


class TokenUsage(BaseModel):
    """Token usage statistics from an LLM call."""

    input_tokens: int = Field(description="Number of input/prompt tokens")
    output_tokens: int = Field(description="Number of output/completion tokens")
    total_tokens: int = Field(description="Total tokens (input + output)")
    cost_usd: float | None = Field(default=None, description="Estimated cost in USD")

    model_config = ConfigDict(extra="forbid")


class LLMChatResponse(BaseModel):
    """Response from LLMProvider.chat()."""

    content: str = Field(default="", description="Response text (empty if streaming)")
    tool_calls: list[dict[str, Any]] | None = Field(
        default=None, description="Tool calls requested by the model"
    )
    thinking: str | None = Field(default=None, description="Model thinking process (if enabled)")
    usage: TokenUsage | None = Field(default=None, description="Token usage (None for streaming)")
    provider: str = Field(description="LLM provider name")
    is_stream: bool = Field(default=False, description="Whether this is a streaming response")
    stream: Any | None = Field(
        default=None,
        description="Async generator for streaming (if is_stream=True)",
        exclude=True,
    )
    instance_id: str | None = Field(
        default=None, description="Load balancer instance ID (set by load balancer path)"
    )
    finish_reason: str | None = Field(
        default=None,
        description=(
            "Normalized finish reason from the provider: stop | length | content_filter | "
            "tool_calls | error | unknown. None for streaming responses (read from done chunk). "
            "length indicates the response was truncated by the token limit."
        ),
    )

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class EmbedResult(BaseModel):
    """Result from LLMProvider.embed()."""

    embedding: list[float] = Field(description="Embedding vector")
    provider: str = Field(description="Embedding provider name")
    usage: TokenUsage | None = Field(default=None, description="Token usage statistics")

    model_config = ConfigDict(extra="forbid")


class BatchEmbedResult(BaseModel):
    """Result from LLMProvider.batch_embed()."""

    embeddings: list[list[float]] = Field(
        description="Embedding vectors (same order as input, empty list for failures)"
    )
    total: int = Field(description="Total texts processed")
    failed: int = Field(description="Number of failed embeddings")
    provider: str = Field(description="Embedding provider name")

    model_config = ConfigDict(extra="forbid")


class ToolResult(BaseModel):
    """Result from LLMProvider.execute_tool()."""

    result: Any = Field(description="Tool execution result")
    tool_name: str = Field(description="Name of the executed tool")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Health Check Models
# ============================================================================


class HealthResult(BaseModel):
    """Health check result for a single provider."""

    status: str = Field(description="Health status: 'healthy' or 'unhealthy'")
    provider: str | None = Field(default=None, description="Provider name")
    model: str | None = Field(default=None, description="Model name")
    embedding_dimensions: int | None = Field(
        default=None, description="Embedding dimensions (embedding provider only)"
    )
    response_time_ms: int | None = Field(default=None, description="Response time in milliseconds")
    error: str | None = Field(default=None, description="Error message if unhealthy")

    model_config = ConfigDict(extra="forbid")


class HealthReport(BaseModel):
    """Combined health report from LLMProvider.check_health()."""

    chat: HealthResult = Field(description="Chat provider health")

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Export All Models
# ============================================================================

__all__ = [
    "AnalysisDepth",
    "BatchEmbedResult",
    "ChunkingResult",
    "ChunksResult",
    "DatabaseInfo",
    "DatabaseStats",
    "Edge",
    "EdgeCreate",
    "EdgeUpdate",
    "EmbedResult",
    "EngineSearchResult",
    "ExtractionResult",
    "HealthReport",
    "HealthResult",
    "IndexingResult",
    "LLMChatResponse",
    "Node",
    "NodeCreate",
    "NodePosition",
    "NodeUpdate",
    "PaginatedResult",
    "ProcessingResult",
    "ProgressCallback",
    "ProgressStage",
    "PropertyDefinition",
    "PropertyType",
    "RebuildResult",
    "SearchMode",
    "Source",
    "SourceErrorStage",
    "SourceStatus",
    "StepToolType",
    "Template",
    "TemplateCreate",
    "TemplateUpdate",
    "TokenUsage",
    "ToolResult",
]


# ============================================================================
# Callback Types (defined after models so Union can reference them)
# ============================================================================

ProgressCallback = Callable[
    [ProgressStage, ChunkingResult | IndexingResult | ExtractionResult],
    None,
]
"""Unified progress callback for document processing pipelines.

Invoked after each pipeline stage with the stage name and typed result::

    def on_progress(stage: ProgressStage, result) -> None:
        print(f"[{stage}] complete")

    await engine.add_document("paper.pdf", on_progress=on_progress)
"""
