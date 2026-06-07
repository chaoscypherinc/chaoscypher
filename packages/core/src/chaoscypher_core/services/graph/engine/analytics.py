# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Analytics Service.

Provides graph analysis by delegating to specialized modules.

Extracted from graph_analytics.py - main service with delegation pattern.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.graph.engine.algorithms import (
    calculate_betweenness_centrality,
    calculate_clustering_coefficient,
    calculate_pagerank,
    detect_communities,
)
from chaoscypher_core.services.graph.engine.graph_metrics import (
    calculate_node_degrees_simple,
    find_isolated_nodes_simple,
)
from chaoscypher_core.services.graph.engine.traversal import (
    build_adjacency,
    find_bridges,
    find_shortest_path,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)


class GraphAnalyticsService:
    """Advanced graph analysis algorithms.

    Provides network analysis capabilities by delegating to specialized modules.
    """

    def __init__(self, graph_repository: GraphRepositoryProtocol):
        """Initialize graph analytics service.

        Args:
            graph_repository: GraphRepository implementation

        """
        self.graph = graph_repository

    # Delegate to traversal module
    def build_adjacency(
        self, nodes: list[Any], edges: list[Any]
    ) -> tuple[dict[str, set[str]], dict[str, int]]:
        """Build adjacency list from nodes and edges."""
        return build_adjacency(nodes, edges)

    def find_shortest_path(
        self, nodes: list[Any], edges: list[Any], source_id: str, target_id: str
    ) -> dict[str, Any]:
        """Find shortest path between two nodes."""
        return find_shortest_path(nodes, edges, source_id, target_id)

    def find_bridges(self, nodes: list[Any], edges: list[Any]) -> dict[str, Any]:
        """Find bridge edges in the graph."""
        return find_bridges(nodes, edges)

    # Delegate to algorithms module
    def detect_communities(
        self, nodes: list[Any], edges: list[Any], max_iterations: int = 100
    ) -> dict[str, Any]:
        """Detect communities using connected components."""
        return detect_communities(nodes, edges, max_iterations)

    def calculate_pagerank(
        self,
        nodes: list[Any],
        edges: list[Any],
        damping: float = 0.85,
        max_iterations: int = 100,
        tolerance: float = 1e-6,
        personalization: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Calculate PageRank scores for nodes."""
        return calculate_pagerank(nodes, edges, damping, max_iterations, tolerance, personalization)

    def calculate_betweenness_centrality(
        self, nodes: list[Any], edges: list[Any]
    ) -> dict[str, Any]:
        """Calculate betweenness centrality for all nodes."""
        return calculate_betweenness_centrality(nodes, edges)

    def calculate_clustering_coefficient(
        self, nodes: list[Any], edges: list[Any]
    ) -> dict[str, Any]:
        """Calculate clustering coefficient for the graph."""
        return calculate_clustering_coefficient(nodes, edges)

    # Static methods delegate to statistics module
    @staticmethod
    def calculate_node_degrees_simple(edges: list[Any]) -> dict[str, int]:
        """Calculate simple degree counts from edge list."""
        return calculate_node_degrees_simple(edges)

    @staticmethod
    def find_isolated_nodes_simple(nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
        """Find nodes with no connections."""
        return find_isolated_nodes_simple(nodes, edges)
