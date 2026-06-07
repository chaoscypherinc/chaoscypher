# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edge Tool Handlers.

Handles edge creation and listing operations.

Extracted from tool_executor.py for SRP compliance.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import EdgeCreate
from chaoscypher_core.services.workflows.tools.engine.handlers.decorators import tool_handler
from chaoscypher_core.settings import GraphSettings


if TYPE_CHECKING:
    from chaoscypher_core.models import Edge, EdgeWithNodes
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)

# Load graph settings for default template IDs and relationship type
_GRAPH = GraphSettings()
_DEFAULT_EDGE_TEMPLATE = _GRAPH.default_edge_template
_DEFAULT_RELATIONSHIP_TYPE = _GRAPH.default_relationship_type


class EdgeToolHandlers:
    """Handles all edge-related tool operations."""

    def __init__(self, graph_repository: GraphRepositoryProtocol):
        """Initialize the instance.

        Args:
            graph_repository: Repository for graph operations.

        """
        self.graph = graph_repository

    @tool_handler("create_edge_failed")
    async def create_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        template_id: str = _DEFAULT_EDGE_TEMPLATE,
        label: str = _DEFAULT_RELATIONSHIP_TYPE,
        properties: dict[str, Any] | None = None,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Create an edge between nodes."""
        if properties is None:
            properties = {}

        edge = self.graph.create_edge(
            EdgeCreate(
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                template_id=template_id,
                label=label,
                properties=properties,
            )
        )

        return {
            "success": True,
            "message": f"Created edge: {label}",
            "edge_id": edge.id,
            "edge": {
                "id": edge.id,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "label": edge.label,
                "properties": edge.properties,
            },
        }

    @tool_handler("list_edges_failed")
    async def list_edges(
        self,
        node_id: str | None = None,
        limit: int = 100,
        source_ids: list[str] | None = None,
    ) -> dict:
        """List edges, optionally filtered by node and source scope."""
        edges = self.graph.list_edges(limit=limit)

        # Filter by node if requested
        if node_id:
            edges = [e for e in edges if node_id in (e.source_node_id, e.target_node_id)]

        # Filter by source scope — only include edges between in-scope nodes
        if source_ids:
            all_node_ids = set()
            for edge in edges:
                all_node_ids.add(edge.source_node_id)
                all_node_ids.add(edge.target_node_id)
            nodes = self.graph.get_nodes_batch(list(all_node_ids))
            allowed_ids = {
                n.id
                for n in nodes
                if not getattr(n, "source_id", None) or n.source_id in source_ids
            }
            edges = [
                e
                for e in edges
                if e.source_node_id in allowed_ids and e.target_node_id in allowed_ids
            ]

        return {
            "success": True,
            "count": len(edges),
            "edges": [
                {
                    "id": edge.id,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "label": edge.label,
                    "template_id": edge.template_id,
                    "properties": edge.properties,
                }
                for edge in edges
            ],
        }

    @tool_handler("get_node_edges_failed")
    async def get_node_edges(
        self,
        node_id: str,
        direction: str = "both",
        edge_type: str | None = None,
        limit: int = 50,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Get all edges connected to a node with direction and type filtering.

        Args:
            node_id: The ID of the node to get edges for
            direction: Edge direction - "outgoing", "incoming", or "both"
            edge_type: Filter by edge label or template_id (optional)
            limit: Maximum edges to return (default 50)
            source_ids: Optional source scope filter

        Returns:
            Dict with edges and connected node details

        """
        # Query edges directly by node_id using repository filters
        outgoing_edges: list[Edge] | list[EdgeWithNodes] = []
        incoming_edges: list[Edge] | list[EdgeWithNodes] = []

        if direction in ("outgoing", "both"):
            outgoing_edges = self.graph.list_edges(source_node_id=node_id, limit=limit)

        if direction in ("incoming", "both"):
            incoming_edges = self.graph.list_edges(target_node_id=node_id, limit=limit)

        # Combine and deduplicate (in case of self-loops)
        all_edges = outgoing_edges + incoming_edges
        seen_ids = set()
        unique_edges = []
        for edge in all_edges:
            if edge.id not in seen_ids:
                seen_ids.add(edge.id)
                unique_edges.append(edge)

        # Apply edge type filter if specified
        filtered_edges = []
        related_node_ids: set[str] = set()

        for edge in unique_edges:
            # Edge type filtering (match label or template_id)
            if edge_type and edge_type not in (edge.label, edge.template_id):
                continue

            filtered_edges.append(edge)

            # Track related node IDs
            is_source = edge.source_node_id == node_id
            if is_source:
                related_node_ids.add(edge.target_node_id)
            else:
                related_node_ids.add(edge.source_node_id)

            if len(filtered_edges) >= limit:
                break

        # Batch fetch related nodes
        related_nodes = self.graph.get_nodes_batch(list(related_node_ids))
        # Filter related nodes by source scope
        if source_ids:
            related_nodes = [
                n
                for n in related_nodes
                if not getattr(n, "source_id", None) or n.source_id in source_ids
            ]
        nodes_dict = {node.id: node for node in related_nodes}

        # Build result with node details
        edges_with_nodes = []
        for edge in filtered_edges:
            is_outgoing = edge.source_node_id == node_id
            related_node_id = edge.target_node_id if is_outgoing else edge.source_node_id
            related_node = nodes_dict.get(related_node_id)
            # Skip edges to out-of-scope nodes
            if source_ids and related_node_id not in nodes_dict:
                continue

            edges_with_nodes.append(
                {
                    "edge_id": edge.id,
                    "label": edge.label,
                    "template_id": edge.template_id,
                    "direction": "outgoing" if is_outgoing else "incoming",
                    "related_node": {
                        "id": related_node.id,
                        "label": related_node.label,
                        "template_id": related_node.template_id,
                        "properties": related_node.properties,
                    }
                    if related_node
                    else {"id": related_node_id, "label": "[not found]"},
                    "properties": edge.properties,
                }
            )

        return {
            "success": True,
            "node_id": node_id,
            "direction": direction,
            "edge_type_filter": edge_type,
            "count": len(edges_with_nodes),
            "edges": edges_with_nodes,
        }
