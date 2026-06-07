# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Grounding Service for MCP Integration.

Read-only graph query service for external AI agents.

This module contains the GroundingService class, which provides lightweight,
read-only access to the knowledge graph for Model Context Protocol (MCP)
compatible AI agents. It allows agents to discover, explore, and query
knowledge nodes and their relationships without mutating the graph.
"""

from typing import TYPE_CHECKING, cast

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_cortex.features.graph.models import (
    GroundingEdgeListResponse,
    GroundingNodeListResponse,
    NeighborNodeResponse,
    NeighborsResponse,
    NodeWithEdgesResponse,
)
from chaoscypher_cortex.shared.api.models import PaginationMetadata


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.models import Edge

__all__ = ["GroundingService"]


class GroundingService:
    """Grounding Service for MCP Integration.

    Provides read-only graph query operations optimized for AI agent consumption.
    All methods are non-mutating and focused on knowledge discovery.

    Use Cases:
    - AI agents discovering relevant knowledge nodes
    - External systems querying graph structure
    - MCP-compatible tools retrieving context
    - Research and exploration workflows
    """

    def __init__(self, graph_repository: GraphRepository, settings: Settings):
        """Initialize GroundingService.

        Args:
            graph_repository: Repository for RDF graph operations
            settings: Application settings instance

        """
        self.graph_repository = graph_repository
        self.settings = settings

    def search_nodes(
        self,
        q: str | None = None,
        template_id: str | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> GroundingNodeListResponse:
        """Search and list nodes with filtering.

        Args:
            q: Search query (filters by label if provided)
            template_id: Filter by template ID
            page: 1-based page number
            page_size: Items per page (defaults to settings.pagination.default_page_size)

        Returns:
            Paginated nodes envelope with ``data`` and ``pagination`` fields.

        MCP Use Case:
            AI agents can discover relevant knowledge nodes by:
            - Searching for specific topics (q parameter)
            - Filtering by entity type (template_id parameter)
            - Paginating through large result sets

        Note:
            ``q`` is applied in Python after the SQL page is fetched, so
            ``pagination.total`` reflects the SQL-filtered count
            (``template_id`` only) — not the post-filtered subset. Agents
            iterating with ``q`` should keep paging until ``has_next`` is false.

        """
        effective_page_size = (
            page_size if page_size is not None else self.settings.pagination.default_page_size
        )
        effective_page_size = min(effective_page_size, self.settings.pagination.max_page_size)
        skip = (page - 1) * effective_page_size

        nodes = self.graph_repository.list_nodes(
            template_id=template_id, skip=skip, limit=effective_page_size
        )

        # Apply text search filter if query provided
        if q:
            q_lower = q.lower()
            nodes = [
                node
                for node in nodes
                if q_lower in node.label.lower()
                or any(q_lower in str(v).lower() for v in node.properties.values())
            ]

        # Total reflects the SQL-filtered count (template_id), the same scan
        # the repository walked. Computing exact totals across the q post-filter
        # would require loading every page, which defeats pagination.
        if template_id is not None:
            total = self.graph_repository.count_nodes_by_template([template_id])
        else:
            total = self.graph_repository.count_nodes()

        total_pages = (total + effective_page_size - 1) // effective_page_size if total > 0 else 1
        return GroundingNodeListResponse(
            data=nodes,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                page_size=effective_page_size,
                total_pages=total_pages,
                has_next=page < total_pages,
                has_prev=page > 1,
            ),
        )

    def get_node_with_edges(self, node_id: str) -> NodeWithEdgesResponse:
        """Get a single node with all its connected edges.

        Args:
            node_id: Node ID to retrieve

        Returns:
            Node with incoming and outgoing edges

        Raises:
            NotFoundError: if node not found

        MCP Use Case:
            AI agents can retrieve complete context for a node including:
            - All node properties and metadata
            - Outgoing edges (what this node relates to)
            - Incoming edges (what relates to this node)
            - Full relationship graph for local context

        """
        # Get the node
        node = self.graph_repository.get_node(node_id)
        if not node:
            raise NotFoundError("Node", node_id)

        # Query edges filtered by this node at the SQL level.
        # ``with_nodes`` defaults to False, so the union return type is
        # always ``list[Edge]`` at runtime — cast narrows the static type.
        edge_limit = self.settings.batching.edge_list_limit
        outgoing_edges = cast(
            "list[Edge]",
            self.graph_repository.list_edges(source_node_id=node_id, skip=0, limit=edge_limit),
        )
        incoming_edges = cast(
            "list[Edge]",
            self.graph_repository.list_edges(target_node_id=node_id, skip=0, limit=edge_limit),
        )

        return NodeWithEdgesResponse(
            node=node,
            outgoing_edges=outgoing_edges,
            incoming_edges=incoming_edges,
            total_outgoing=len(outgoing_edges),
            total_incoming=len(incoming_edges),
        )

    def search_edges(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> GroundingEdgeListResponse:
        """Search and list edges with filtering.

        Args:
            source_node_id: Filter by source node ID
            target_node_id: Filter by target node ID
            page: 1-based page number
            page_size: Items per page (defaults to settings.pagination.default_page_size)

        Returns:
            Paginated edges envelope with ``data`` and ``pagination`` fields.

        MCP Use Case:
            AI agents can discover relationships by:
            - Finding all connections from a specific node (source_node_id)
            - Finding all connections to a specific node (target_node_id)
            - Exploring relationship patterns across the graph

        """
        effective_page_size = (
            page_size if page_size is not None else self.settings.pagination.default_page_size
        )
        effective_page_size = min(effective_page_size, self.settings.pagination.max_page_size)
        skip = (page - 1) * effective_page_size

        # Pass both filters to SQL — no Python-side post-filtering.
        # ``with_nodes`` defaults to False so the return is always
        # ``list[Edge]`` at runtime; cast narrows the static union.
        edges = cast(
            "list[Edge]",
            self.graph_repository.list_edges(
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                skip=skip,
                limit=effective_page_size,
            ),
        )
        total = self.graph_repository.count_edges(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )

        total_pages = (total + effective_page_size - 1) // effective_page_size if total > 0 else 1
        return GroundingEdgeListResponse(
            data=edges,
            pagination=PaginationMetadata(
                total=total,
                page=page,
                page_size=effective_page_size,
                total_pages=total_pages,
                has_next=page < total_pages,
                has_prev=page > 1,
            ),
        )

    def get_node_neighbors(
        self, node_id: str, direction: str = "both", limit: int | None = None
    ) -> NeighborsResponse:
        """Get nodes connected to this node via edges.

        Uses SQL-filtered edge queries and batch node fetching to avoid
        full table scans and N+1 query patterns.

        Args:
            node_id: Node ID to find neighbors for
            direction: Direction to follow edges ("outgoing", "incoming", "both")
            limit: Maximum neighbors to return

        Returns:
            List of neighbor nodes with relationship information

        Raises:
            NotFoundError: if node not found
            ValidationError: if direction is invalid

        MCP Use Case:
            AI agents can traverse the knowledge graph by:
            - Following outgoing edges (what this node relates to)
            - Following incoming edges (what relates to this node)
            - Exploring full neighborhood (both directions)
            - Understanding connection patterns and relationship types

        """
        effective_limit = (
            limit if limit is not None else self.settings.pagination.default_list_limit
        )
        # Validate direction parameter
        valid_directions = ["outgoing", "incoming", "both"]
        if direction not in valid_directions:
            msg = f"Invalid direction. Must be one of: {', '.join(valid_directions)}"
            raise ValidationError(msg, field="direction")

        # Verify node exists
        node = self.graph_repository.get_node(node_id)
        if not node:
            raise NotFoundError("Node", node_id)

        edge_limit = self.settings.batching.edge_list_limit

        # Collect edges and neighbor IDs using SQL-filtered queries
        neighbor_edges: list[tuple[str, str, Edge]] = []  # (neighbor_id, direction, edge)

        if direction in ["outgoing", "both"]:
            outgoing = self.graph_repository.list_edges(
                source_node_id=node_id, skip=0, limit=edge_limit
            )
            neighbor_edges.extend((edge.target_node_id, "outgoing", edge) for edge in outgoing)

        if direction in ["incoming", "both"]:
            incoming = self.graph_repository.list_edges(
                target_node_id=node_id, skip=0, limit=edge_limit
            )
            neighbor_edges.extend((edge.source_node_id, "incoming", edge) for edge in incoming)

        # Deduplicate neighbor IDs and apply limit
        seen_node_ids: set[str] = set()
        unique_edges: list[tuple[str, str, Edge]] = []
        for neighbor_id, edge_dir, edge in neighbor_edges:
            if neighbor_id not in seen_node_ids:
                seen_node_ids.add(neighbor_id)
                unique_edges.append((neighbor_id, edge_dir, edge))
                if len(unique_edges) >= effective_limit:
                    break

        # Batch-fetch all neighbor nodes in a single query
        neighbor_ids = [nid for nid, _, _ in unique_edges]
        node_map = {n.id: n for n in self.graph_repository.get_nodes_batch(neighbor_ids)}

        # Build response from pre-fetched nodes
        neighbors: list[NeighborNodeResponse] = []
        for neighbor_id, edge_dir, edge in unique_edges:
            neighbor_node = node_map.get(neighbor_id)
            if neighbor_node:
                neighbors.append(
                    NeighborNodeResponse(
                        node=neighbor_node,
                        relationship_type=edge.label,
                        edge_id=edge.id,
                        direction=edge_dir,
                        edge_properties=edge.properties,
                    )
                )

        return NeighborsResponse(
            node_id=node_id,
            neighbors=neighbors,
            total=len(neighbors),
            direction=direction,
        )
