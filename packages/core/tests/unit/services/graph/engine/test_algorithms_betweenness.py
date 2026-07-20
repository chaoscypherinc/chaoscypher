# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for betweenness centrality return-shape consistency."""

from types import SimpleNamespace

from chaoscypher_core.services.graph.engine.algorithms import (
    calculate_betweenness_centrality,
)


def _make_node(node_id: str, label: str = "") -> SimpleNamespace:
    """Create a minimal node object with .id and .label attributes."""
    return SimpleNamespace(id=node_id, label=label or node_id)


def _make_edge(source_id: str, target_id: str) -> SimpleNamespace:
    """Create a minimal edge object with .source_node_id and .target_node_id."""
    return SimpleNamespace(source_node_id=source_id, target_node_id=target_id)


class TestBetweennessReturnShape:
    """The empty-graph branch must return the same keys as the populated path."""

    def test_empty_graph_uses_canonical_keys(self) -> None:
        """An empty graph returns betweenness_scores/top_nodes, not a legacy key."""
        result = calculate_betweenness_centrality([], [])
        assert result == {"betweenness_scores": {}, "top_nodes": []}

    def test_populated_and_empty_share_key_set(self) -> None:
        """Empty and populated results expose an identical top-level key set.

        Regression guard: the empty branch previously returned ``{"betweenness": {}}``
        while the populated path returned ``{"betweenness_scores", "top_nodes"}``,
        so a caller keying on either shape crashed on the other.
        """
        nodes = [_make_node("a"), _make_node("b"), _make_node("c")]
        edges = [_make_edge("a", "b"), _make_edge("b", "c")]

        populated = calculate_betweenness_centrality(nodes, edges)
        empty = calculate_betweenness_centrality([], [])

        assert set(populated.keys()) == set(empty.keys())
        assert "betweenness_scores" in populated
        assert "b" in populated["betweenness_scores"]
