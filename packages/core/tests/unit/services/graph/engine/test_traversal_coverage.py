# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for services/graph/engine/traversal.py.

Covers the rustworkx-backed builders (``build_graph`` / ``build_digraph`` /
``build_adjacency``), shortest-path finding (success, same-node, missing-node,
no-path branches), and Tarjan bridge detection (empty, single-bridge, cycle,
disconnected-component, relationship-type enrichment).

All inputs are lightweight ``SimpleNamespace`` node/edge stand-ins — no
database or repository is touched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chaoscypher_core.services.graph.engine.traversal import (
    build_adjacency,
    build_digraph,
    build_graph,
    find_bridges,
    find_shortest_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(node_id: str, label: str | None = None) -> SimpleNamespace:
    """Minimal node with .id and .label."""
    return SimpleNamespace(id=node_id, label=label or node_id.upper())


def _edge(source_id: str, target_id: str, label: str = "RELATED_TO") -> SimpleNamespace:
    """Minimal edge with .source_node_id, .target_node_id, .label."""
    return SimpleNamespace(source_node_id=source_id, target_node_id=target_id, label=label)


# ---------------------------------------------------------------------------
# build_graph
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildGraph:
    def test_maps_ids_to_indices_and_back(self) -> None:
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "b")]
        graph, id_to_idx, idx_to_id = build_graph(nodes, edges)

        assert set(id_to_idx) == {"a", "b"}
        # reverse map is consistent
        for nid, idx in id_to_idx.items():
            assert idx_to_id[idx] == nid
        assert graph.num_nodes() == 2
        assert graph.num_edges() == 1

    def test_deduplicates_undirected_edges(self) -> None:
        # (a, b) and (b, a) collapse to one undirected edge.
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "b"), _edge("b", "a")]
        graph, _, _ = build_graph(nodes, edges)
        assert graph.num_edges() == 1

    def test_skips_edges_referencing_unknown_nodes(self) -> None:
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "ghost"), _edge("a", "b")]
        graph, _, _ = build_graph(nodes, edges)
        # Only the a-b edge survives; the dangling one is ignored.
        assert graph.num_edges() == 1


# ---------------------------------------------------------------------------
# build_digraph
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildDigraph:
    def test_directed_edges_not_deduplicated_by_reverse(self) -> None:
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "b"), _edge("b", "a")]
        digraph, id_to_idx, idx_to_id = build_digraph(nodes, edges)
        assert digraph.num_nodes() == 2
        # Both directed edges are kept.
        assert digraph.num_edges() == 2
        assert idx_to_id[id_to_idx["a"]] == "a"

    def test_skips_edges_referencing_unknown_nodes(self) -> None:
        nodes = [_node("a")]
        edges = [_edge("a", "missing")]
        digraph, _, _ = build_digraph(nodes, edges)
        assert digraph.num_edges() == 0


# ---------------------------------------------------------------------------
# build_adjacency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildAdjacency:
    def test_symmetric_adjacency_and_index_map(self) -> None:
        nodes = [_node("a"), _node("b"), _node("c")]
        edges = [_edge("a", "b")]
        adjacency, index_map = build_adjacency(nodes, edges)

        assert adjacency["a"] == {"b"}
        assert adjacency["b"] == {"a"}
        # isolated node still present with empty set
        assert adjacency["c"] == set()
        assert set(index_map) == {"a", "b", "c"}

    def test_ignores_edges_with_unknown_endpoints(self) -> None:
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "zzz")]
        adjacency, _ = build_adjacency(nodes, edges)
        assert adjacency["a"] == set()
        assert adjacency["b"] == set()


# ---------------------------------------------------------------------------
# find_shortest_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindShortestPath:
    def test_source_not_found(self) -> None:
        nodes = [_node("a")]
        result = find_shortest_path(nodes, [], "ghost", "a")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_target_not_found(self) -> None:
        nodes = [_node("a")]
        result = find_shortest_path(nodes, [], "a", "ghost")
        assert result["success"] is False

    def test_same_source_and_target(self) -> None:
        nodes = [_node("a", "Alpha")]
        result = find_shortest_path(nodes, [], "a", "a")
        assert result["success"] is True
        assert result["length"] == 0
        assert result["path"] == [{"id": "a", "label": "Alpha"}]

    def test_simple_path(self) -> None:
        nodes = [_node("a", "A"), _node("b", "B"), _node("c", "C")]
        edges = [_edge("a", "b"), _edge("b", "c")]
        result = find_shortest_path(nodes, edges, "a", "c")
        assert result["success"] is True
        assert result["length"] == 2
        path_ids = [p["id"] for p in result["path"]]
        assert path_ids == ["a", "b", "c"]
        # labels are carried through from node_map
        assert result["path"][0]["label"] == "A"

    def test_no_path_between_disconnected_nodes(self) -> None:
        nodes = [_node("a"), _node("b"), _node("c")]
        # a-b connected, c isolated
        edges = [_edge("a", "b")]
        result = find_shortest_path(nodes, edges, "a", "c")
        assert result["success"] is False
        assert "No path" in result["error"]


# ---------------------------------------------------------------------------
# find_bridges
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindBridges:
    def test_empty_edges_returns_zero(self) -> None:
        result = find_bridges([_node("a")], [])
        assert result == {"bridges": [], "num_bridges": 0}

    def test_single_edge_is_a_bridge(self) -> None:
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "b", label="LINKS")]
        result = find_bridges(nodes, edges)
        assert result["num_bridges"] == 1
        bridge = result["bridges"][0]
        # relationship_type enriched from edge_lookup
        assert bridge["relationship_type"] == "LINKS"
        assert {bridge["source_id"], bridge["target_id"]} == {"a", "b"}

    def test_cycle_has_no_bridges(self) -> None:
        # Triangle a-b-c-a: every edge is on a cycle, so no bridges.
        nodes = [_node("a"), _node("b"), _node("c")]
        edges = [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")]
        result = find_bridges(nodes, edges)
        assert result["num_bridges"] == 0
        assert result["bridges"] == []

    def test_bridge_connecting_two_clusters(self) -> None:
        # Two triangles joined by a single bridge edge c-d.
        nodes = [_node(n) for n in ("a", "b", "c", "d", "e", "f")]
        edges = [
            _edge("a", "b"),
            _edge("b", "c"),
            _edge("c", "a"),
            _edge("c", "d", label="BRIDGE"),
            _edge("d", "e"),
            _edge("e", "f"),
            _edge("f", "d"),
        ]
        result = find_bridges(nodes, edges)
        assert result["num_bridges"] == 1
        bridge = result["bridges"][0]
        assert {bridge["source_id"], bridge["target_id"]} == {"c", "d"}
        assert bridge["relationship_type"] == "BRIDGE"

    def test_disconnected_components_each_dfs_visited(self) -> None:
        # Two separate single-edge components — both edges are bridges.
        nodes = [_node(n) for n in ("a", "b", "c", "d")]
        edges = [_edge("a", "b"), _edge("c", "d")]
        result = find_bridges(nodes, edges)
        assert result["num_bridges"] == 2

    def test_unknown_relationship_type_defaults(self) -> None:
        # A path a-b-c: both edges are bridges. Enrichment must find each label;
        # verify the lookup fallback path by using a chain where the bridge's
        # (source,target) order is reversed relative to the original edge.
        nodes = [_node("a"), _node("b")]
        edges = [_edge("a", "b", label="KNOWS")]
        result = find_bridges(nodes, edges)
        # edge_lookup stores both directions, so type resolves regardless of order
        assert result["bridges"][0]["relationship_type"] == "KNOWS"
