# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Statistics Calculators - Module-Level Functions for Package Stats.

Pure business logic for calculating export package statistics.
All functions are stateless utilities with zero backend dependencies.

Functions:
- calculate_template_stats: Template usage and complexity stats
- calculate_knowledge_stats: Graph metrics, embeddings, dates
- calculate_lens_stats: Lens transformations and mapping stats
- calculate_workflow_stats: Workflow and trigger statistics
- calculate_source_stats: Document source and chunking stats

Example:
    from chaoscypher_core.services.export.engine.stats import calculate_template_stats

"""

from __future__ import annotations

import contextlib
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.export.models.schemas import (
    ChunkingConfig,
    DateRange,
    EmbeddingStats,
    KnowledgeStats,
    LensStats,
    SourceStats,
    TemplateStats,
    WorkflowStats,
)


logger = structlog.get_logger(__name__)


__all__ = [
    "calculate_knowledge_stats",
    "calculate_lens_stats",
    "calculate_source_stats",
    "calculate_template_stats",
    "calculate_workflow_stats",
]


# ---------------------------------------------------------------------------
# Template stats
# ---------------------------------------------------------------------------


def calculate_template_stats(templates: list[dict]) -> TemplateStats:
    """Calculate statistics for templates.

    Counts node and edge templates, calculates average properties
    per template, and identifies the most complex template.

    Args:
        templates: List of template dicts.

    Returns:
        TemplateStats object with calculated statistics.

    Example:
        >>> stats = calculate_template_stats(templates)
        >>> print(f"Avg properties: {stats.avg_properties_per_template}")

    """
    property_counts = [len(t.get("properties", [])) for t in templates]
    avg_props = sum(property_counts) / len(property_counts) if property_counts else 0.0

    most_complex = None
    if property_counts:
        max_props = max(property_counts)
        for t in templates:
            if len(t.get("properties", [])) == max_props:
                most_complex = t.get("name")
                break

    return TemplateStats(
        avg_properties_per_template=round(avg_props, 2),
        most_complex_template=most_complex,
    )


# ---------------------------------------------------------------------------
# Knowledge stats
# ---------------------------------------------------------------------------


def calculate_knowledge_stats(
    nodes: list[dict],
    edges: list[dict],
    settings: EngineSettings,
    include_embeddings: bool = False,
) -> KnowledgeStats:
    """Calculate comprehensive statistics for knowledge graph.

    Counts nodes and edges by type, calculates graph metrics (degree,
    density, isolated nodes), extracts date ranges, and computes
    embedding statistics.

    Args:
        nodes: Knowledge nodes.
        edges: Knowledge edges.
        settings: Export settings (for embedding info).
        include_embeddings: Whether embedding vectors are included in the export.

    Returns:
        KnowledgeStats object with calculated statistics.

    Example:
        >>> stats = calculate_knowledge_stats(nodes, edges, settings)
        >>> print(f"Nodes: {stats.node_count}, Edges: {stats.edge_count}")
        >>> print(f"Density: {stats.graph_density}, Isolated: {stats.isolated_node_count}")

    """
    # Relationship types
    relationship_types: dict[str, int] = defaultdict(int)
    for edge in edges:
        relationship_types[edge.get("label", "Unknown")] += 1

    # Node degree calculation
    node_degrees: dict[str, int] = defaultdict(int)
    for edge in edges:
        source_id = edge.get("source_node_id")
        target_id = edge.get("target_node_id")
        if source_id:
            node_degrees[source_id] += 1
        if target_id:
            node_degrees[target_id] += 1

    max_degree = max(node_degrees.values()) if node_degrees else 0
    isolated_node_count = len(nodes) - len(node_degrees)
    total_degree = sum(node_degrees.values())
    avg_degree = (total_degree / len(nodes)) if len(nodes) > 0 else 0.0

    # Graph density
    graph_density = len(edges) / (len(nodes) * (len(nodes) - 1)) if len(nodes) > 1 else 0.0

    # Date range
    timestamps = []
    for item in nodes + edges:
        if "created_at" in item:
            try:
                if isinstance(item["created_at"], str):
                    timestamps.append(
                        datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                    )
                elif isinstance(item["created_at"], datetime):
                    timestamps.append(item["created_at"])
            except Exception:
                logger.debug("timestamp_parse_failed")

    date_range = DateRange(
        earliest=min(timestamps) if timestamps else None,
        latest=max(timestamps) if timestamps else None,
    )

    # Embedding statistics
    nodes_with_embeddings = sum(1 for node in nodes if node.get("embedding"))
    has_embeddings = nodes_with_embeddings > 0
    embedding_dimensions = None
    embedding_model = None
    embedding_provider = None

    if has_embeddings:
        for node in nodes:
            if node.get("embedding"):
                embedding_dimensions = len(node["embedding"])
                break

        embedding_model = settings.embedding.model
        embedding_provider = settings.embedding.provider

    # Coverage metrics
    total_embeddable_items = len(nodes)  # All nodes should have embeddings
    coverage_pct = (
        round((nodes_with_embeddings / total_embeddable_items) * 100, 2)
        if total_embeddable_items > 0
        else 0.0
    )

    # Extract model version from model name if present (e.g., "nomic-embed-text:v1.5" -> "v1.5")
    model_version = None
    if embedding_model and ":" in embedding_model:
        model_version = embedding_model.split(":")[-1]

    embedding_stats = EmbeddingStats(
        is_present=has_embeddings,
        vectors_included=include_embeddings and has_embeddings,
        node_count=nodes_with_embeddings,
        dimensions=embedding_dimensions,
        model_name=embedding_model,
        total_embeddable_items=total_embeddable_items,
        coverage_pct=coverage_pct,
        model_version=model_version,
        model_provider=embedding_provider,
    )

    return KnowledgeStats(
        node_count=len(nodes),
        edge_count=len(edges),
        relationship_types=dict(relationship_types),
        avg_degree=round(avg_degree, 2),
        max_degree=max_degree,
        graph_density=round(graph_density, 6),
        isolated_node_count=isolated_node_count,
        date_range=date_range,
        embeddings=embedding_stats,
    )


# ---------------------------------------------------------------------------
# Lens stats
# ---------------------------------------------------------------------------


def calculate_lens_stats(lens_nodes: list[dict]) -> LensStats:
    """Calculate statistics for lenses.

    Counts lenses by input/output template and counts lenses
    with transformation rules.

    Args:
        lens_nodes: List of lens nodes.

    Returns:
        LensStats object with calculated statistics.

    Example:
        >>> stats = calculate_lens_stats(lens_nodes)
        >>> print(f"Total: {stats.total_count}, With Rules: {stats.has_transformation_rules}")

    """
    input_templates: dict[str, int] = defaultdict(int)
    output_templates: dict[str, int] = defaultdict(int)
    has_rules = 0

    for lens in lens_nodes:
        props = lens.get("properties", {})
        if props.get("input_template"):
            input_templates[props["input_template"]] += 1
        if props.get("output_template"):
            output_templates[props["output_template"]] += 1
        if props.get("transformation_rules"):
            has_rules += 1

    return LensStats(
        total_count=len(lens_nodes),
        input_templates=dict(input_templates),
        output_templates=dict(output_templates),
        has_transformation_rules=has_rules,
    )


# ---------------------------------------------------------------------------
# Workflow stats
# ---------------------------------------------------------------------------


def calculate_workflow_stats(nodes: list[dict], triggers: list[dict]) -> WorkflowStats:
    """Calculate statistics for workflows.

    Counts workflows (enabled/disabled), workflow steps, calculates
    averages, counts triggers, and analyzes tool usage across workflows.

    Args:
        nodes: Workflow nodes (includes both workflows and steps).
        triggers: Event triggers.

    Returns:
        WorkflowStats object with calculated statistics.

    Example:
        >>> stats = calculate_workflow_stats(workflow_nodes, triggers)
        >>> print(f"Workflows: {stats.total_workflows}, Steps: {stats.total_steps}")
        >>> print(f"Tools: {stats.tools_count}, Triggers: {stats.trigger_count}")

    """
    workflow_main_nodes = [n for n in nodes if n.get("template_id") == "system_workflow"]
    workflow_step_nodes = [n for n in nodes if n.get("template_id") == "system_workflow_step"]

    enabled_count = sum(
        1 for w in workflow_main_nodes if w.get("properties", {}).get("enabled", True)
    )
    disabled_count = len(workflow_main_nodes) - enabled_count

    avg_steps = len(workflow_step_nodes) / len(workflow_main_nodes) if workflow_main_nodes else 0.0

    tools_used: dict[str, int] = defaultdict(int)
    for step in workflow_step_nodes:
        tool = step.get("properties", {}).get("tool")
        if tool:
            tools_used[tool] += 1

    return WorkflowStats(
        total_workflows=len(workflow_main_nodes),
        enabled_workflows=enabled_count,
        disabled_workflows=disabled_count,
        total_steps=len(workflow_step_nodes),
        avg_steps_per_workflow=round(avg_steps, 2),
        trigger_count=len(triggers),
        tools_used=dict(tools_used),
        tools_count=len(tools_used),
    )


# ---------------------------------------------------------------------------
# Source stats
# ---------------------------------------------------------------------------


def _calculate_chunk_quality_metrics(
    all_chunks: list[dict],
) -> dict[str, Any]:
    """Calculate chunk length distribution metrics.

    Computes average, min, max, standard deviation, and empty count
    from the content lengths of all chunks.

    Args:
        all_chunks: List of chunk dicts with a ``"content"`` key.

    Returns:
        Dict with keys ``avg_chunk_length``, ``min_chunk_length``,
        ``max_chunk_length``, ``chunk_length_std_dev``, ``empty_chunk_count``.

    """
    chunk_lengths = [len(c.get("content", "")) for c in all_chunks]
    empty_chunk_count = sum(1 for length in chunk_lengths if length == 0)

    if chunk_lengths:
        avg_chunk_length = int(statistics.mean(chunk_lengths))
        min_chunk_length = min(chunk_lengths)
        max_chunk_length = max(chunk_lengths)
        chunk_length_std_dev = (
            round(statistics.stdev(chunk_lengths), 2) if len(chunk_lengths) > 1 else 0.0
        )
    else:
        avg_chunk_length = 0
        min_chunk_length = 0
        max_chunk_length = 0
        chunk_length_std_dev = 0.0

    return {
        "avg_chunk_length": avg_chunk_length,
        "min_chunk_length": min_chunk_length,
        "max_chunk_length": max_chunk_length,
        "chunk_length_std_dev": chunk_length_std_dev,
        "empty_chunk_count": empty_chunk_count,
    }


def _calculate_embedding_and_rag_metrics(
    all_chunks: list[dict],
    total_chunks: int,
    sources: list[dict],
    empty_chunk_count: int,
) -> dict[str, Any]:
    """Calculate embedding coverage and RAG readiness metrics.

    Counts chunks with embeddings, computes coverage percentage,
    collects unique embedding models, and determines indexing
    completion and RAG readiness.

    Args:
        all_chunks: List of chunk dicts.
        total_chunks: Total number of chunks.
        sources: List of source dicts with ``"status"`` keys.
        empty_chunk_count: Number of chunks with zero-length content.

    Returns:
        Dict with keys ``chunks_with_embeddings``, ``embedding_coverage_pct``,
        ``embedding_models_used``, ``rag_ready``, ``indexing_complete_pct``.

    """
    chunks_with_embeddings = sum(1 for c in all_chunks if c.get("embedding"))
    embedding_coverage_pct = (
        round((chunks_with_embeddings / total_chunks) * 100, 2) if total_chunks > 0 else 0.0
    )

    embedding_models_set: set[str] = set()
    for chunk in all_chunks:
        if model := chunk.get("embedding_model"):
            embedding_models_set.add(model)

    rag_ready = total_chunks > 0 and embedding_coverage_pct == 100.0 and empty_chunk_count == 0

    indexed_statuses = {
        SourceStatus.INDEXED,
        SourceStatus.COMMITTED,
        SourceStatus.EXTRACTED,
        SourceStatus.EXTRACTING,
    }
    indexed_sources = sum(1 for s in sources if s.get("status") in indexed_statuses)
    indexing_complete_pct = round((indexed_sources / len(sources)) * 100, 2) if sources else 0.0

    return {
        "chunks_with_embeddings": chunks_with_embeddings,
        "embedding_coverage_pct": embedding_coverage_pct,
        "embedding_models_used": sorted(embedding_models_set),
        "rag_ready": rag_ready,
        "indexing_complete_pct": indexing_complete_pct,
    }


def _calculate_citation_metrics(
    all_citations: list[dict],
    source_count: int,
) -> dict[str, Any]:
    """Calculate citation quality metrics.

    Computes average citations per source, average confidence,
    unique entities cited, and extraction method distribution.

    Args:
        all_citations: List of citation dicts.
        source_count: Total number of sources.

    Returns:
        Dict with keys ``avg_citations_per_source``,
        ``avg_citation_confidence``, ``unique_entities_cited``,
        ``extraction_methods``.

    """
    total_citations = len(all_citations)
    avg_citations_per_source = round(total_citations / source_count, 2) if source_count else 0.0

    confidences = [
        c.get("confidence", 0.0) for c in all_citations if c.get("confidence") is not None
    ]
    avg_citation_confidence = round(statistics.mean(confidences), 3) if confidences else 0.0

    entity_uris: set[str] = set()
    for citation in all_citations:
        if entity_uri := citation.get("entity_uri"):
            entity_uris.add(entity_uri)

    extraction_methods: dict[str, int] = dict(
        Counter(c.get("extraction_method", "unknown") for c in all_citations)
    )

    return {
        "avg_citations_per_source": avg_citations_per_source,
        "avg_citation_confidence": avg_citation_confidence,
        "unique_entities_cited": len(entity_uris),
        "extraction_methods": extraction_methods,
    }


def _build_chunking_config(
    chunking_settings: dict[str, Any] | None,
) -> ChunkingConfig | None:
    """Build a ChunkingConfig from raw settings, if provided.

    Args:
        chunking_settings: Chunking configuration dict, or None.

    Returns:
        ChunkingConfig if settings were provided, else None.

    """
    if not chunking_settings:
        return None
    return ChunkingConfig(
        strategy=chunking_settings["strategy"],
        target_chunk_size=chunking_settings["small_chunk_size"],
        chunk_overlap=chunking_settings["small_chunk_overlap"],
        min_chunk_size=chunking_settings["min_chunk_size"],
        max_chunk_size=chunking_settings["max_chunk_size"],
        respect_boundaries=chunking_settings["respect_boundaries"],
        separators=chunking_settings.get("separators", []),
        group_size=chunking_settings["group_size"],
        group_overlap=chunking_settings["group_overlap"],
        auto_group_size=chunking_settings.get("auto_group_size", True),
        normalize_newlines=chunking_settings["normalize_newlines"],
    )


def calculate_source_stats(
    sources: list[dict],
    chunking_settings: dict[str, Any] | None = None,
    include_embeddings: bool = False,
) -> SourceStats:
    """Calculate statistics for sources.

    Counts sources by status, chunks, citations, analyzes source types,
    calculates temporal ranges, chunk quality metrics, embedding coverage,
    RAG readiness, and citation quality metrics.

    Args:
        sources: List of source dictionaries with chunks and citations.
        chunking_settings: Optional chunking configuration used to create chunks.
        include_embeddings: Whether embedding vectors are included in the export.

    Returns:
        SourceStats object with calculated statistics including RAG quality metrics.

    Example:
        >>> stats = calculate_source_stats(sources)
        >>> print(f"Sources: {stats.total_sources}, Chunks: {stats.total_chunks}")
        >>> print(f"RAG Ready: {stats.rag_ready}, Coverage: {stats.embedding_coverage_pct}%")

    """
    if not sources:
        return SourceStats(
            active_sources=0,
            archived_sources=0,
            total_chunks=0,
            total_citations=0,
            source_types={},
            avg_chunks_per_source=0.0,
            total_content_length=0,
            date_range=DateRange(earliest=None, latest=None),
            avg_chunk_length=0,
            min_chunk_length=0,
            max_chunk_length=0,
            chunk_length_std_dev=0.0,
            empty_chunk_count=0,
            chunks_with_embeddings=0,
            embedding_coverage_pct=0.0,
            vectors_included=False,
            rag_ready=False,
            indexing_complete_pct=0.0,
            avg_citations_per_source=0.0,
            avg_citation_confidence=0.0,
            unique_entities_cited=0,
            chunking_config=None,
        )

    # Collect all chunks and citations
    all_chunks: list[dict] = []
    all_citations: list[dict] = []
    for source in sources:
        all_chunks.extend(source.get("chunks", []))
        all_citations.extend(source.get("citations", []))

    # Count by status
    active_count = sum(1 for s in sources if s.get("status") == "active")
    archived_count = len(sources) - active_count

    # Count total chunks and citations
    total_chunks = len(all_chunks)
    total_citations = len(all_citations)

    # Count by source type
    source_types: dict[str, int] = defaultdict(int)
    for source in sources:
        source_type = source.get("source_type", "unknown")
        source_types[source_type] += 1

    # Calculate content length
    total_content_length = sum(s.get("total_content_length", 0) for s in sources)

    # Calculate average chunks per source
    avg_chunks = total_chunks / len(sources) if sources else 0.0

    # Calculate date range
    dates = []
    for source in sources:
        if created_at := source.get("created_at"):
            if isinstance(created_at, str):
                with contextlib.suppress(ValueError, AttributeError):
                    dates.append(datetime.fromisoformat(created_at.replace("Z", "+00:00")))
            elif isinstance(created_at, datetime):
                dates.append(created_at)

    date_range = DateRange(earliest=None, latest=None)
    if dates:
        date_range.earliest = min(dates)
        date_range.latest = max(dates)

    # Delegated metric computations
    chunk_quality = _calculate_chunk_quality_metrics(all_chunks)
    embedding_rag = _calculate_embedding_and_rag_metrics(
        all_chunks, total_chunks, sources, chunk_quality["empty_chunk_count"]
    )
    citation = _calculate_citation_metrics(all_citations, len(sources))

    return SourceStats(
        # Basic counts
        active_sources=active_count,
        archived_sources=archived_count,
        total_chunks=total_chunks,
        total_citations=total_citations,
        source_types=dict(source_types),
        avg_chunks_per_source=round(avg_chunks, 2),
        total_content_length=total_content_length,
        date_range=date_range,
        # Chunk quality metrics
        **chunk_quality,
        # Embedding coverage
        **embedding_rag,
        vectors_included=include_embeddings,
        # Citation quality metrics
        **citation,
        # Chunking configuration
        chunking_config=_build_chunking_config(chunking_settings),
    )
