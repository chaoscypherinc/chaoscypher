# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for Personalized PageRank extension."""

from types import SimpleNamespace

from chaoscypher_core.services.graph.engine.algorithms import calculate_pagerank


def _make_node(node_id: str, label: str = "") -> SimpleNamespace:
    """Create a minimal node object with .id and .label attributes."""
    return SimpleNamespace(id=node_id, label=label or node_id)


def _make_edge(source_id: str, target_id: str) -> SimpleNamespace:
    """Create a minimal edge object with .source_node_id and .target_node_id."""
    return SimpleNamespace(source_node_id=source_id, target_node_id=target_id)


class TestPersonalizedPageRank:
    """Tests for calculate_pagerank() with personalization parameter."""

    def test_no_personalization_unchanged(self) -> None:
        """Without personalization, behavior matches standard PageRank."""
        nodes = [_make_node("a"), _make_node("b"), _make_node("c")]
        edges = [_make_edge("a", "b"), _make_edge("b", "c")]
        result_standard = calculate_pagerank(nodes, edges)
        result_none = calculate_pagerank(nodes, edges, personalization=None)
        assert result_standard["pagerank_scores"] == result_none["pagerank_scores"]

    def test_personalization_biases_seed_node(self) -> None:
        """Personalized PR should bias scores toward the seed node neighborhood."""
        nodes = [_make_node("a"), _make_node("b"), _make_node("c"), _make_node("d")]
        edges = [
            _make_edge("a", "b"),
            _make_edge("b", "c"),
            _make_edge("a", "d"),
        ]
        result = calculate_pagerank(nodes, edges, personalization={"a": 1.0})
        scores = result["pagerank_scores"]
        assert scores["a"] > scores["c"]

    def test_personalization_multiple_seeds(self) -> None:
        """Multiple seed nodes should distribute personalization weight."""
        nodes = [_make_node("a"), _make_node("b"), _make_node("c")]
        edges = [_make_edge("a", "c"), _make_edge("b", "c")]
        result = calculate_pagerank(nodes, edges, personalization={"a": 0.7, "b": 0.3})
        scores = result["pagerank_scores"]
        assert scores["a"] > scores["b"]
        assert "c" in scores

    def test_personalization_disconnected_seed(self) -> None:
        """Seed node with no outgoing edges still gets high score via teleportation."""
        nodes = [_make_node("a"), _make_node("b"), _make_node("c")]
        edges = [_make_edge("b", "c")]
        result = calculate_pagerank(nodes, edges, personalization={"a": 1.0})
        scores = result["pagerank_scores"]
        assert scores["a"] > scores["b"]
        assert scores["a"] > scores["c"]

    def test_personalization_weights_normalized(self) -> None:
        """Unnormalized weights should produce same result as normalized."""
        nodes = [_make_node("a"), _make_node("b")]
        edges = [_make_edge("a", "b")]
        result_raw = calculate_pagerank(nodes, edges, personalization={"a": 5.0, "b": 5.0})
        result_norm = calculate_pagerank(nodes, edges, personalization={"a": 0.5, "b": 0.5})
        for node_id in ["a", "b"]:
            assert (
                abs(
                    result_raw["pagerank_scores"][node_id] - result_norm["pagerank_scores"][node_id]
                )
                < 1e-6
            )

    def test_personalization_unknown_seed_ignored(self) -> None:
        """Seed nodes not in the graph should be silently ignored."""
        nodes = [_make_node("a"), _make_node("b")]
        edges = [_make_edge("a", "b")]
        result = calculate_pagerank(nodes, edges, personalization={"a": 0.8, "z": 0.2})
        scores = result["pagerank_scores"]
        assert "a" in scores
        assert "b" in scores
        assert "z" not in scores

    def test_empty_graph_with_personalization(self) -> None:
        """Empty graph should return empty scores even with personalization."""
        result = calculate_pagerank([], [], personalization={"a": 1.0})
        assert result["pagerank_scores"] == {}
        assert result["top_nodes"] == []
