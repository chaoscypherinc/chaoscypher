# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for Reciprocal Rank Fusion utility."""

from chaoscypher_core.utils.rrf import reciprocal_rank_fusion


class TestReciprocalRankFusion:
    """Tests for reciprocal_rank_fusion()."""

    def test_single_list_preserves_order(self) -> None:
        """Single result list should maintain its ranking order."""
        results = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
        merged = reciprocal_rank_fusion(results)
        ids = [r[0] for r in merged]
        assert ids == ["a", "b", "c"]

    def test_two_lists_merge_by_rrf_score(self) -> None:
        """Items appearing in both lists should score higher than single-list items."""
        list_a = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
        list_b = [("b", 0.95), ("d", 0.85), ("a", 0.75)]
        merged = reciprocal_rank_fusion(list_a, list_b)
        ids = [r[0] for r in merged]
        assert ids[0] == "b"
        assert ids[1] == "a"
        assert set(ids) == {"a", "b", "c", "d"}

    def test_empty_lists_return_empty(self) -> None:
        """Empty input should return empty output."""
        assert reciprocal_rank_fusion() == []
        assert reciprocal_rank_fusion([]) == []

    def test_disjoint_lists_interleave(self) -> None:
        """Non-overlapping lists should interleave by rank position."""
        list_a = [("a", 0.9), ("b", 0.8)]
        list_b = [("c", 0.95), ("d", 0.85)]
        merged = reciprocal_rank_fusion(list_a, list_b)
        ids = [r[0] for r in merged]
        assert set(ids[:2]) == {"a", "c"}
        assert set(ids[2:]) == {"b", "d"}

    def test_custom_k_parameter(self) -> None:
        """Different k values should not change relative ordering of shared items."""
        list_a = [("a", 0.9), ("b", 0.8)]
        list_b = [("b", 0.95), ("a", 0.75)]
        merged_k60 = reciprocal_rank_fusion(list_a, list_b, k=60)
        merged_k1 = reciprocal_rank_fusion(list_a, list_b, k=1)
        ids_k60 = [r[0] for r in merged_k60]
        ids_k1 = [r[0] for r in merged_k1]
        assert ids_k60 == ids_k1

    def test_three_lists_merge(self) -> None:
        """Three result lists should merge correctly."""
        list_a = [("a", 0.9)]
        list_b = [("a", 0.8), ("b", 0.7)]
        list_c = [("b", 0.95), ("a", 0.6), ("c", 0.5)]
        merged = reciprocal_rank_fusion(list_a, list_b, list_c)
        ids = [r[0] for r in merged]
        assert ids[0] == "a"
        assert "c" in ids
