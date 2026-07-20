# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Analysis Algorithms (rustworkx-accelerated).

Implements community detection, PageRank, betweenness centrality,
and clustering coefficient calculations using rustworkx compiled
Rust graph algorithms for performance at scale.

Extracted from graph_analytics.py for SRP compliance.
"""

from typing import Any

import rustworkx as rx
import structlog

from chaoscypher_core.services.graph.engine.traversal import (
    build_adjacency,
    build_digraph,
    build_graph,
)


logger = structlog.get_logger(__name__)


def detect_communities(
    nodes: list[Any], edges: list[Any], max_iterations: int = 100
) -> dict[str, Any]:
    """Detect communities using connected component analysis.

    Uses rustworkx compiled BFS for O(V+E) connected component detection.

    Args:
        nodes: List of node objects.
        edges: List of edge objects.
        max_iterations: Maximum iterations (unused, kept for API compat).

    Returns:
        Dict with community assignments.

    """
    if not nodes:
        return {"communities": [], "num_communities": 0}

    graph, _id_to_idx, idx_to_id = build_graph(nodes, edges)

    # rustworkx returns list of sets of node indices
    components = rx.connected_components(graph)

    communities_list = []
    community_assignments: dict[str, int] = {}

    for comm_id, component_indices in enumerate(components):
        members = []
        for idx in component_indices:
            node_id = idx_to_id[idx]
            community_assignments[node_id] = comm_id
            # Find the node object for its label
            members.append({"id": node_id, "label": graph[idx]})
        communities_list.append({"id": comm_id, "size": len(members), "members": members})

    return {
        "num_communities": len(communities_list),
        "communities": communities_list,
    }


def calculate_pagerank(
    nodes: list[Any],
    edges: list[Any],
    damping: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6,
    personalization: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Calculate PageRank scores using rustworkx compiled power iteration.

    Supports Personalized PageRank (PPR) via the ``personalization`` parameter.
    When provided, the teleportation distribution is biased toward seed nodes
    instead of being uniform across all nodes. This is the core mechanism used
    by GraphRAG to surface entities structurally close to query-relevant seeds.

    Args:
        nodes: List of node objects with ``.id`` and ``.label`` attributes.
        edges: List of edge objects with ``.source_node_id`` and
            ``.target_node_id`` attributes.
        damping: Damping factor (probability of following a link). Typically
            0.85.
        max_iterations: Maximum number of power-iteration steps.
        tolerance: Convergence threshold on the maximum score change per
            iteration.
        personalization: Optional mapping of node ID to non-negative seed
            weight. Weights are normalized to sum to 1.0 internally. Node IDs
            not present in the graph are silently ignored. When ``None``,
            standard uniform teleportation is used.

    Returns:
        Dict with keys:
            - ``pagerank_scores``: Mapping of node ID to final score.
            - ``top_nodes``: List of up to 20 highest-scoring node dicts,
              each containing ``id``, ``label``, and ``pagerank``.
            - ``iterations``: The configured ``max_iterations`` value.
              rustworkx does not expose the actual number of power-iteration
              steps taken before convergence, so this reports the cap, not
              the realized step count.

    """
    if not nodes:
        return {"pagerank_scores": {}, "top_nodes": [], "iterations": 0}

    digraph, id_to_idx, idx_to_id = build_digraph(nodes, edges)

    # Build personalization dict using graph indices
    rx_personalization: dict[int, float] | None = None
    if personalization is not None:
        valid_seeds = {
            id_to_idx[node_id]: weight
            for node_id, weight in personalization.items()
            if node_id in id_to_idx and weight > 0
        }
        if valid_seeds:
            rx_personalization = valid_seeds

    # rustworkx pagerank returns CentralityMapping {node_idx: score}
    scores = rx.pagerank(
        digraph,
        alpha=damping,
        max_iter=max_iterations,
        tol=tolerance,
        personalization=rx_personalization,
    )

    # Convert index-based scores to node ID-based scores
    pagerank: dict[str, float] = {}
    for idx, score in scores.items():
        pagerank[idx_to_id[idx]] = score

    # Build node label map for output
    node_labels = {node.id: node.label for node in nodes}

    ranked_nodes = sorted(
        [
            {"id": nid, "label": node_labels[nid], "pagerank": score}
            for nid, score in pagerank.items()
        ],
        key=lambda x: x["pagerank"],
        reverse=True,
    )

    return {
        "pagerank_scores": pagerank,
        "top_nodes": ranked_nodes[:20],
        "iterations": max_iterations,  # rustworkx doesn't expose iteration count
    }


def calculate_betweenness_centrality(nodes: list[Any], edges: list[Any]) -> dict[str, Any]:
    """Calculate betweenness centrality using rustworkx compiled Brandes algorithm.

    Betweenness measures how often a node appears on shortest paths
    between other nodes. Uses compiled Rust implementation for O(V*E)
    performance instead of pure Python.

    Args:
        nodes: List of node objects.
        edges: List of edge objects.

    Returns:
        Dict with betweenness scores.

    """
    if not nodes:
        return {"betweenness_scores": {}, "top_nodes": []}

    graph, _id_to_idx, idx_to_id = build_graph(nodes, edges)

    # rustworkx returns CentralityMapping {node_idx: score}
    scores = rx.betweenness_centrality(graph, normalized=True)

    betweenness: dict[str, float] = {}
    for idx, score in scores.items():
        betweenness[idx_to_id[idx]] = score

    node_labels = {node.id: node.label for node in nodes}

    ranked_nodes = sorted(
        [
            {"id": nid, "label": node_labels[nid], "betweenness": score}
            for nid, score in betweenness.items()
        ],
        key=lambda x: x["betweenness"],
        reverse=True,
    )

    return {"betweenness_scores": betweenness, "top_nodes": ranked_nodes[:20]}


def calculate_clustering_coefficient(nodes: list[Any], edges: list[Any]) -> dict[str, Any]:
    """Calculate clustering coefficient.

    Measures how much nodes tend to cluster together. Uses rustworkx
    for adjacency building and frozenset optimization for triangle
    counting.

    Args:
        nodes: List of node objects.
        edges: List of edge objects.

    Returns:
        Dict with clustering coefficients.

    """
    if not nodes:
        return {"clustering_coefficients": {}, "average_clustering": 0.0, "top_nodes": []}

    adjacency, _ = build_adjacency(nodes, edges)

    # Pre-convert to frozensets for O(1) membership tests in inner loop
    adjacency_sets = {nid: frozenset(neighbors) for nid, neighbors in adjacency.items()}

    clustering = {}

    for node in nodes:
        neighbors = list(adjacency_sets[node.id])
        k = len(neighbors)

        if k < 2:
            clustering[node.id] = 0.0
            continue

        # Count edges between neighbors
        edges_between = 0
        for i, neighbor1 in enumerate(neighbors):
            for neighbor2 in neighbors[i + 1 :]:
                if neighbor2 in adjacency_sets[neighbor1]:
                    edges_between += 1

        # Clustering coefficient
        max_edges = k * (k - 1) / 2
        clustering[node.id] = edges_between / max_edges if max_edges > 0 else 0.0

    avg_clustering = sum(clustering.values()) / len(clustering) if clustering else 0.0

    return {
        "clustering_coefficients": clustering,
        "average_clustering": avg_clustering,
        "top_nodes": sorted(
            [
                {"id": node.id, "label": node.label, "clustering": clustering[node.id]}
                for node in nodes
            ],
            key=lambda x: x["clustering"],
            reverse=True,
        )[:20],
    }
