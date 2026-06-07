# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Knowledge Loader - Imports knowledge nodes and edges from CCX packages.

Handles importing knowledge graph data from knowledge.jsonld files.

Example:
    from chaoscypher_core.services.package.importer.loaders import KnowledgeLoader

    loader = KnowledgeLoader(graph_repository)
    loader.load(knowledge_data, mapper, stats, "default")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import EdgeCreate, NodeCreate
from chaoscypher_core.services.package.importer.loaders.base import PackageLoaderBase


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.services.package.importer.models import IdMapper, ImportStats


logger = structlog.get_logger(__name__)


class KnowledgeLoader(PackageLoaderBase):
    """Loads knowledge nodes and edges from CCX packages.

    Handles importing knowledge graph data from knowledge.jsonld files.
    Must be called after TemplateLoader since nodes reference templates.

    Attributes:
        graph_repository: Graph repository for node/edge operations.
    """

    def __init__(self, graph_repository: GraphRepository) -> None:
        """Initialize knowledge loader.

        Args:
            graph_repository: Graph repository for node/edge operations.
        """
        self.graph_repository = graph_repository

    def load(
        self,
        data: dict[str, Any] | list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load knowledge nodes and edges from parsed knowledge.jsonld data.

        Args:
            data: Parsed knowledge.jsonld data {"nodes": [...], "edges": [...]}.
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name.

        Raises:
            ValueError: If data format is invalid.
        """
        if not isinstance(data, dict):
            stats.errors.append("Invalid knowledge.jsonld format: expected dict")
            return

        nodes_data = data.get("nodes", [])
        edges_data = data.get("edges", [])

        logger.info(
            "loading_knowledge",
            node_count=len(nodes_data),
            edge_count=len(edges_data),
        )

        # Load nodes first (edges reference nodes)
        self._load_nodes(nodes_data, mapper, stats, database_name)

        # Load edges after nodes
        self._load_edges(edges_data, mapper, stats, database_name)

        logger.info(
            "knowledge_loaded",
            nodes_imported=stats.nodes_imported,
            edges_imported=stats.edges_imported,
        )

    def _load_nodes(
        self,
        nodes_data: list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load nodes from parsed data.

        Args:
            nodes_data: List of node dictionaries.
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name.
        """
        nodes_to_create: list[NodeCreate] = []
        original_ids: list[str] = []

        for node_data in nodes_data:
            original_id = node_data.get("id")
            if not original_id:
                stats.warnings.append("Skipping node without id")
                continue

            # Get template ID - support both direct template_id and template_name lookup
            template_id = node_data.get("template_id")
            if not template_id:
                # Fall back to template_name lookup for backwards compatibility
                template_name = node_data.get("template_name")
                if template_name:
                    template_id = mapper.get_template_id(template_name)
                    if not template_id:
                        # Try to find template by name in repository
                        templates = self.graph_repository.list_templates()
                        template = next((t for t in templates if t.name == template_name), None)
                        if template:
                            template_id = template.id
                            mapper.map_template(template_name, template_id)

            try:
                # Skip if no valid template_id
                if not template_id or not isinstance(template_id, str):
                    stats.warnings.append(
                        f"Skipping node '{original_id}' without valid template_id"
                    )
                    continue

                # Use 'label' field (export format) with fallback to 'name' for compatibility
                label = node_data.get("label") or node_data.get("name", "Unnamed Node")

                # Parse position if present (NodePosition from export)
                position_data = node_data.get("position")
                position = None
                if position_data and isinstance(position_data, dict):
                    from chaoscypher_core.models import NodePosition

                    position = NodePosition(
                        x=position_data.get("x", 0),
                        y=position_data.get("y", 0),
                    )

                node_create = NodeCreate(
                    label=label,
                    template_id=template_id,
                    properties=node_data.get("properties", {}),
                    position=position,
                    embedding=node_data.get("embedding"),
                )
                nodes_to_create.append(node_create)
                original_ids.append(original_id)
            except Exception as e:
                stats.warnings.append(f"Invalid node '{original_id}': {e}")

        # Create nodes and track ID mappings
        for idx, node_create in enumerate(nodes_to_create):
            try:
                created_node = self.graph_repository.create_node(node_create)
                original_id = original_ids[idx]
                mapper.map_node(original_id, created_node.id)
                stats.nodes_imported += 1
                logger.debug(
                    "node_imported",
                    original_id=original_id,
                    new_id=created_node.id,
                    label=created_node.label,
                )
            except Exception as e:
                stats.errors.append(f"Failed to create node: {e}")

        # Detect missing embeddings
        nodes_without_embeddings = sum(
            1 for node_data in nodes_data if not node_data.get("embedding")
        )
        if nodes_without_embeddings > 0:
            stats.embeddings_need_regeneration = True
            if not stats.embedding_mismatch_reason:
                stats.embedding_mismatch_reason = (
                    f"{nodes_without_embeddings} knowledge nodes imported without embeddings"
                )

    def _load_edges(
        self,
        edges_data: list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load edges from parsed data.

        Args:
            edges_data: List of edge dictionaries.
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name.
        """
        for edge_data in edges_data:
            original_source = edge_data.get("source_node_id")
            original_target = edge_data.get("target_node_id")

            if not original_source or not original_target:
                stats.warnings.append("Skipping edge without source or target")
                continue

            # Get remapped node IDs
            new_source = mapper.get_node_id(original_source)
            new_target = mapper.get_node_id(original_target)

            if not new_source:
                stats.warnings.append(f"Edge source node not found: {original_source}")
                continue

            if not new_target:
                stats.warnings.append(f"Edge target node not found: {original_target}")
                continue

            # Get template ID - support both direct template_id and template_name lookup
            template_id = edge_data.get("template_id")
            if not template_id:
                # Fall back to template_name lookup for backwards compatibility
                template_name = edge_data.get("template_name")
                if template_name:
                    template_id = mapper.get_template_id(template_name)

            try:
                # Skip if no valid template_id
                if not template_id or not isinstance(template_id, str):
                    stats.warnings.append(
                        f"Skipping edge without valid template_id: {original_source} -> {original_target}"
                    )
                    continue

                # Use 'label' field (export format) with fallback to 'name' for compatibility
                label = edge_data.get("label") or edge_data.get("name", "")
                edge_create = EdgeCreate(
                    source_node_id=new_source,
                    target_node_id=new_target,
                    template_id=template_id,
                    label=label,
                    properties=edge_data.get("properties", {}),
                )
                self.graph_repository.create_edge(edge_create)
                stats.edges_imported += 1
                logger.debug(
                    "edge_imported",
                    source=new_source,
                    target=new_target,
                )
            except Exception as e:
                stats.warnings.append(f"Failed to create edge: {e}")


__all__ = ["KnowledgeLoader"]
