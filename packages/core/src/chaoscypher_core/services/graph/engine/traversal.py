# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Traversal Operations (rustworkx-accelerated).

Handles adjacency building, shortest path finding, and bridge detection.
Uses rustworkx compiled Rust graph algorithms for graph construction and
BFS traversal. Bridge detection uses Tarjan's O(V+E) algorithm instead
of naive O(E*(V+E)) connectivity testing.

Extracted from graph_analytics.py for SRP compliance.
"""

from collections import defaultdict
from typing import Any

import rustworkx as rx
import structlog


logger = structlog.get_logger(__name__)


def build_graph(
    nodes: list[Any], edges: list[Any]
) -> tuple[rx.PyGraph, dict[str, int], dict[int, str]]:
    """Build an undirected rustworkx graph from nodes and edges.

    Args:
        nodes: List of node objects with ``.id`` and ``.label`` attributes.
        edges: List of edge objects with ``.source_node_id`` and
            ``.target_node_id`` attributes.

    Returns:
        Tuple of (graph, id_to_idx, idx_to_id) where id_to_idx maps node
        string IDs to rustworkx integer indices and idx_to_id is the reverse.

    """
    graph = rx.PyGraph()
    id_to_idx: dict[str, int] = {}
    node_ids: set[str] = set()

    for node in nodes:
        idx = graph.add_node(node.label)
        id_to_idx[node.id] = idx
        node_ids.add(node.id)

    idx_to_id = {v: k for k, v in id_to_idx.items()}

    added_edges: set[tuple[str, str]] = set()
    for edge in edges:
        src, tgt = edge.source_node_id, edge.target_node_id
        if src in id_to_idx and tgt in id_to_idx:
            pair = (min(src, tgt), max(src, tgt))
            if pair not in added_edges:
                graph.add_edge(id_to_idx[src], id_to_idx[tgt], None)
                added_edges.add(pair)

    return graph, id_to_idx, idx_to_id


def build_digraph(
    nodes: list[Any], edges: list[Any]
) -> tuple[rx.PyDiGraph, dict[str, int], dict[int, str]]:
    """Build a directed rustworkx graph from nodes and edges.

    Args:
        nodes: List of node objects with ``.id`` and ``.label`` attributes.
        edges: List of edge objects with ``.source_node_id`` and
            ``.target_node_id`` attributes.

    Returns:
        Tuple of (digraph, id_to_idx, idx_to_id) where id_to_idx maps node
        string IDs to rustworkx integer indices and idx_to_id is the reverse.

    """
    digraph = rx.PyDiGraph()
    id_to_idx: dict[str, int] = {}
    node_ids: set[str] = set()

    for node in nodes:
        idx = digraph.add_node(node.label)
        id_to_idx[node.id] = idx
        node_ids.add(node.id)

    idx_to_id = {v: k for k, v in id_to_idx.items()}

    for edge in edges:
        src, tgt = edge.source_node_id, edge.target_node_id
        if src in id_to_idx and tgt in id_to_idx:
            digraph.add_edge(id_to_idx[src], id_to_idx[tgt], None)

    return digraph, id_to_idx, idx_to_id


def build_adjacency(
    nodes: list[Any], edges: list[Any]
) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Build adjacency list from nodes and edges.

    Retained for callers that need dict-based adjacency (e.g. clustering
    coefficient triangle counting). For new code, prefer ``build_graph``
    or ``build_digraph``.

    Args:
        nodes: List of node objects.
        edges: List of edge objects.

    Returns:
        Tuple of (adjacency_dict, node_index_map).

    """
    adjacency: dict[str, set[str]] = defaultdict(set)
    node_ids = {node.id for node in nodes}

    for edge in edges:
        if edge.source_node_id in node_ids and edge.target_node_id in node_ids:
            adjacency[edge.source_node_id].add(edge.target_node_id)
            # Treat as undirected for most analyses
            adjacency[edge.target_node_id].add(edge.source_node_id)

    # Ensure all nodes are in adjacency dict
    for node in nodes:
        if node.id not in adjacency:
            adjacency[node.id] = set()

    return adjacency, {node.id: i for i, node in enumerate(nodes)}


def find_shortest_path(
    nodes: list[Any], edges: list[Any], source_id: str, target_id: str
) -> dict[str, Any]:
    """Find shortest path between two nodes using rustworkx compiled BFS.

    Args:
        nodes: List of node objects.
        edges: List of edge objects.
        source_id: Source node ID.
        target_id: Target node ID.

    Returns:
        Dict with path information.

    """
    node_map = {node.id: node for node in nodes}
    if source_id not in node_map or target_id not in node_map:
        return {"success": False, "error": "Source or target node not found"}

    if source_id == target_id:
        return {
            "success": True,
            "path": [{"id": source_id, "label": node_map[source_id].label}],
            "length": 0,
        }

    graph, id_to_idx, idx_to_id = build_graph(nodes, edges)

    src_idx = id_to_idx[source_id]
    tgt_idx = id_to_idx[target_id]

    # rustworkx compiled Dijkstra shortest path (Rust)
    paths = rx.dijkstra_shortest_paths(graph, src_idx, target=tgt_idx)
    if tgt_idx not in paths:
        return {"success": False, "error": "No path found between nodes"}

    path_indices = paths[tgt_idx]
    final_path = [
        {"id": idx_to_id[idx], "label": node_map[idx_to_id[idx]].label} for idx in path_indices
    ]

    return {
        "success": True,
        "path": final_path,
        "length": len(final_path) - 1,
    }


def find_bridges(nodes: list[Any], edges: list[Any]) -> dict[str, Any]:
    """Find bridge edges whose removal disconnects the graph.

    Uses Tarjan's DFS-based algorithm for O(V+E) bridge detection,
    replacing the naive O(E*(V+E)) approach of testing each edge removal.

    Args:
        nodes: List of node objects.
        edges: List of edge objects.

    Returns:
        Dict with bridge edges.

    """
    if not edges:
        return {"bridges": [], "num_bridges": 0}

    adjacency, _ = build_adjacency(nodes, edges)
    node_ids = [node.id for node in nodes]

    # Tarjan's bridge-finding algorithm — O(V + E)
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    bridges: list[dict[str, Any]] = []
    timer = [0]

    def dfs(u: str) -> None:
        """Tarjan DFS visit for bridge detection."""
        disc[u] = low[u] = timer[0]
        timer[0] += 1

        for v in adjacency[u]:
            if v not in disc:
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])
                if low[v] > disc[u]:
                    bridges.append({"source_id": u, "target_id": v})
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    # Run DFS from each unvisited node (handles disconnected graphs)
    for nid in node_ids:
        if nid not in disc:
            parent[nid] = None
            dfs(nid)

    # Enrich bridges with relationship type from original edges
    edge_lookup: dict[tuple[str, str], str] = {}
    for edge in edges:
        edge_lookup[(edge.source_node_id, edge.target_node_id)] = edge.label
        edge_lookup[(edge.target_node_id, edge.source_node_id)] = edge.label

    for bridge in bridges:
        key = (bridge["source_id"], bridge["target_id"])
        bridge["relationship_type"] = edge_lookup.get(key, "unknown")

    return {"num_bridges": len(bridges), "bridges": bridges}
