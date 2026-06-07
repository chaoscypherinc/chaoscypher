# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data extraction from graph and source repositories for export.

Provides helper functions that extract, filter, and classify nodes, edges,
templates, triggers, and sources from graph data into categorised buckets
suitable for CCX package creation.  All functions are pure (no side-effects
beyond logging) and operate on plain dicts so they stay framework-agnostic.
"""

from typing import Any

import structlog


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Node / edge separation
# ---------------------------------------------------------------------------


def separate_nodes_and_edges(
    *,
    graph_data: dict[str, Any],
    include_templates: bool,
    include_knowledge: bool,
    include_lenses: bool,
    include_workflows: bool,
    include_sources: bool,
    include_embeddings: bool = False,
    lens_id: str | None,
    safe_get_triggers: Any,
    safe_get_sources: Any,
    sources_repository: Any,
    workflow_db: Any,
) -> dict[str, Any]:
    """Separate nodes and edges into categories based on user selection.

    Args:
        graph_data: Full graph data export.
        include_templates: Include user templates.
        include_knowledge: Include knowledge nodes/edges.
        include_lenses: Include lens nodes/edges.
        include_workflows: Include workflow nodes/edges.
        include_sources: Include document sources.
        include_embeddings: Whether to include embedding vectors.
        lens_id: Optional - Export only a specific lens by ID.
        safe_get_triggers: Callable returning trigger list.
        safe_get_sources: Callable returning source list.
        sources_repository: SourcesProtocol instance (may be ``None``).
        workflow_db: WorkflowDatabase instance (may be ``None``).

    Returns:
        Dict with separated data categories.

    """
    all_nodes = graph_data["nodes"]
    all_edges = graph_data["edges"]

    # Filter out system templates
    user_templates = filter_user_templates(graph_data["templates"], include_templates)

    # Collect user workflow IDs (first pass)
    user_workflow_ids = collect_user_workflow_ids(all_nodes, include_workflows)

    # Classify nodes (second pass)
    node_result = classify_nodes(
        all_nodes,
        include_lenses,
        include_knowledge,
        include_workflows,
        user_workflow_ids,
        lens_id,
    )

    # Classify edges
    edge_result = classify_edges(
        all_edges,
        node_result["lens_node_ids"],
        node_result["workflow_node_ids"],
        include_lenses,
        include_knowledge,
        include_workflows,
    )

    # Get triggers for user workflows
    triggers = collect_triggers(
        include_workflows, user_workflow_ids, workflow_db, safe_get_triggers
    )

    # Get sources with related data
    sources = collect_sources(
        include_sources,
        sources_repository,
        safe_get_sources,
        include_embeddings=include_embeddings,
    )

    return {
        "templates": user_templates,
        "knowledge_nodes": node_result["knowledge_nodes"],
        "knowledge_edges": edge_result["knowledge_edges"],
        "lens_nodes": node_result["lens_nodes"],
        "lens_edges": edge_result["lens_edges"],
        "workflow_nodes": node_result["workflow_nodes"],
        "workflow_edges": edge_result["workflow_edges"],
        "triggers": triggers,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------


def filter_user_templates(
    templates: list[dict[str, Any]], include_templates: bool
) -> list[dict[str, Any]]:
    """Filter out system templates, keeping only user-created ones.

    Args:
        templates: All templates from graph.
        include_templates: Whether to include templates.

    Returns:
        List of user templates (non-system).

    """
    if not include_templates:
        return []
    return [t for t in templates if not t.get("is_system", False)]


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------


def collect_user_workflow_ids(all_nodes: list[dict[str, Any]], include_workflows: bool) -> set[str]:
    """Collect IDs of non-system workflows (first pass).

    Args:
        all_nodes: All nodes from graph.
        include_workflows: Whether workflows are included.

    Returns:
        Set of user workflow IDs.

    """
    if not include_workflows:
        return set()

    user_workflow_ids: set[str] = set()
    system_workflow_count = 0

    for node in all_nodes:
        if node.get("template_id") == "system_workflow":
            is_system = node.get("properties", {}).get("is_system", False)
            if not is_system:
                user_workflow_ids.add(node["id"])
            else:
                system_workflow_count += 1

    if system_workflow_count > 0:
        logger.info("excluding_system_workflows", system_workflow_count=system_workflow_count)

    return user_workflow_ids


# ---------------------------------------------------------------------------
# Node classification
# ---------------------------------------------------------------------------

# Maps system template IDs to their export category.  Anything not listed
# here is treated as "knowledge".
_TEMPLATE_CATEGORY: dict[str, str] = {
    "system_lens": "lens",
    "system_workflow": "workflow",
    "system_workflow_step": "workflow",
}


def _is_lens_included(
    node: dict[str, Any],
    include_lenses: bool,
    lens_id: str | None,
) -> bool:
    """Return whether a lens node should be included in the export."""
    if not include_lenses:
        return False
    # If lens_id is specified, only include that specific lens
    return not (lens_id and node["id"] != lens_id)


def _is_workflow_included(
    node: dict[str, Any],
    template_id: str,
    include_workflows: bool,
    user_workflow_ids: set[str],
) -> bool:
    """Return whether a workflow/step node should be included in the export."""
    if not include_workflows:
        return False
    if template_id == "system_workflow":
        # Only include user-created workflows
        return node["id"] in user_workflow_ids
    # system_workflow_step — only include steps belonging to user workflows
    parent_workflow_id = node.get("properties", {}).get("workflow_id")
    return parent_workflow_id in user_workflow_ids


def classify_nodes(
    all_nodes: list[dict[str, Any]],
    include_lenses: bool,
    include_knowledge: bool,
    include_workflows: bool,
    user_workflow_ids: set[str],
    lens_id: str | None,
) -> dict[str, Any]:
    """Classify nodes into knowledge, lens, and workflow categories.

    Args:
        all_nodes: All nodes from graph.
        include_lenses: Whether to include lens nodes.
        include_knowledge: Whether to include knowledge nodes.
        include_workflows: Whether to include workflow nodes.
        user_workflow_ids: Set of user (non-system) workflow IDs.
        lens_id: Optional specific lens ID to export.

    Returns:
        Dict with classified nodes and ID sets.

    """
    knowledge_nodes: list[dict[str, Any]] = []
    lens_nodes: list[dict[str, Any]] = []
    workflow_nodes: list[dict[str, Any]] = []
    lens_node_ids: set[str] = set()
    workflow_node_ids: set[str] = set()

    buckets: dict[str, tuple[list[dict[str, Any]], set[str]]] = {
        "lens": (lens_nodes, lens_node_ids),
        "workflow": (workflow_nodes, workflow_node_ids),
    }

    include_checks = {
        "lens": lambda node, _tid: _is_lens_included(node, include_lenses, lens_id),
        "workflow": lambda node, tid: _is_workflow_included(
            node, tid, include_workflows, user_workflow_ids
        ),
    }

    for node in all_nodes:
        template_id = node.get("template_id")
        category = _TEMPLATE_CATEGORY.get(template_id)  # type: ignore[arg-type]

        if category is not None:
            if include_checks[category](node, template_id):
                nodes_list, ids_set = buckets[category]
                nodes_list.append(node)
                ids_set.add(node["id"])
        elif include_knowledge:
            knowledge_nodes.append(node)

    return {
        "knowledge_nodes": knowledge_nodes,
        "lens_nodes": lens_nodes,
        "workflow_nodes": workflow_nodes,
        "lens_node_ids": lens_node_ids,
        "workflow_node_ids": workflow_node_ids,
    }


# ---------------------------------------------------------------------------
# Edge classification
# ---------------------------------------------------------------------------


def classify_edges(
    all_edges: list[dict[str, Any]],
    lens_node_ids: set[str],
    workflow_node_ids: set[str],
    include_lenses: bool,
    include_knowledge: bool,
    include_workflows: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Classify edges based on their endpoint node types.

    Args:
        all_edges: All edges from graph.
        lens_node_ids: Set of lens node IDs.
        workflow_node_ids: Set of workflow node IDs.
        include_lenses: Whether to include lens edges.
        include_knowledge: Whether to include knowledge edges.
        include_workflows: Whether to include workflow edges.

    Returns:
        Dict with classified edges.

    """
    knowledge_edges: list[dict[str, Any]] = []
    lens_edges: list[dict[str, Any]] = []
    workflow_edges: list[dict[str, Any]] = []

    for edge in all_edges:
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")

        if source in lens_node_ids or target in lens_node_ids:
            if include_lenses:
                lens_edges.append(edge)
        elif source in workflow_node_ids or target in workflow_node_ids:
            if include_workflows:
                workflow_edges.append(edge)
        elif include_knowledge:
            knowledge_edges.append(edge)

    return {
        "knowledge_edges": knowledge_edges,
        "lens_edges": lens_edges,
        "workflow_edges": workflow_edges,
    }


# ---------------------------------------------------------------------------
# Trigger / source collection
# ---------------------------------------------------------------------------


def collect_triggers(
    include_workflows: bool,
    user_workflow_ids: set[str],
    workflow_db: Any,
    safe_get_triggers: Any,
) -> list[dict[str, Any]]:
    """Collect triggers for user workflows.

    Args:
        include_workflows: Whether workflows are included.
        user_workflow_ids: Set of user workflow IDs.
        workflow_db: WorkflowDatabase instance (may be ``None``).
        safe_get_triggers: Callable returning all triggers.

    Returns:
        List of triggers for user workflows.

    """
    if not include_workflows or not workflow_db:
        return []

    all_triggers = safe_get_triggers()
    return [t for t in all_triggers if t.get("workflow_id") in user_workflow_ids]


def collect_sources(
    include_sources: bool,
    sources_repository: Any,
    safe_get_sources: Any,
    include_embeddings: bool = False,
) -> list[dict[str, Any]]:
    """Collect sources with their chunks, citations, and tags.

    Args:
        include_sources: Whether sources are included.
        sources_repository: SourcesProtocol instance (may be ``None``).
        safe_get_sources: Callable returning source list.
        include_embeddings: Whether to include embedding vectors in chunks.

    Returns:
        List of source dictionaries with related data.

    """
    from chaoscypher_core.services.export.management.metadata_manager import (
        build_source_dict,
    )

    if not include_sources or not sources_repository:
        return []

    source_dicts = safe_get_sources()
    sources = []

    for source in source_dicts:
        source_id = source["id"]
        source_dict = build_source_dict(source)
        source_dict["chunks"] = collect_source_chunks(
            sources_repository, source_id, include_embeddings=include_embeddings
        )
        source_dict["citations"] = collect_source_citations(sources_repository, source_id)
        source_dict["tags"] = collect_source_tags(sources_repository, source_id)
        sources.append(source_dict)

        logger.info(
            "source_exported",
            source_id=source_id,
            chunk_count=len(source_dict["chunks"]),
            citation_count=len(source_dict["citations"]),
            tag_count=len(source_dict["tags"]),
        )

    return sources


def collect_source_chunks(
    sources_repository: Any,
    source_id: str,
    include_embeddings: bool = False,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Collect all chunks for a source with pagination.

    Args:
        sources_repository: SourcesProtocol instance.
        source_id: Source ID.
        include_embeddings: Include embedding data (default False for export).
        page_size: Number of chunks per page.

    Returns:
        List of chunk dictionaries.

    """
    from chaoscypher_core.services.export.management.metadata_manager import (
        build_chunk_dict,
    )

    all_chunks: list[dict[str, Any]] = []
    page = 1

    while True:
        chunks, total = sources_repository.get_chunks_by_source(
            source_id=source_id,
            page=page,
            page_size=page_size,
            status=None,
            include_embeddings=include_embeddings,
        )
        if not chunks:
            break

        all_chunks.extend(
            build_chunk_dict(chunk, include_embeddings=include_embeddings) for chunk in chunks
        )

        if len(all_chunks) >= total:
            break
        page += 1

    return all_chunks


def collect_source_citations(
    sources_repository: Any,
    source_id: str,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Collect all citations for a source with pagination.

    Args:
        sources_repository: SourcesProtocol instance.
        source_id: Source ID.
        page_size: Number of citations per page.

    Returns:
        List of citation dictionaries.

    """
    from chaoscypher_core.services.export.management.metadata_manager import (
        build_citation_dict,
    )

    all_citations: list[dict[str, Any]] = []
    page = 1

    while True:
        citations, total = sources_repository.get_citations_by_source(
            source_id=source_id, page=page, page_size=page_size
        )
        if not citations:
            break

        all_citations.extend(build_citation_dict(citation) for citation in citations)

        if len(all_citations) >= total:
            break
        page += 1

    return all_citations


def collect_source_tags(
    sources_repository: Any,
    source_id: str,
) -> list[dict[str, Any]]:
    """Collect tags for a source.

    Args:
        sources_repository: SourcesProtocol instance.
        source_id: Source ID.

    Returns:
        List of tag dictionaries.

    """
    source_tags = sources_repository.get_source_tags(source_id)
    return [
        {
            "tag_id": tag["id"],
            "tag_name": tag.get("name"),
            "tag_color": tag.get("color"),
            "tag_description": tag.get("description"),
        }
        for tag in source_tags
    ]
