# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the v7 quality scoring formula."""

from __future__ import annotations

import pytest

from chaoscypher_core.services.quality.scoring import (
    DEFAULT_TARGET_DENSITY,
    SCORING_VERSION,
    calculate_density_score,
    calculate_hub_skew,
    calculate_quality_grade,
    calculate_reciprocal_rate,
    calculate_structural_penalty,
)


@pytest.mark.unit
class TestScoringVersion:
    def test_scoring_version_is_v7(self) -> None:
        assert SCORING_VERSION == 7


@pytest.mark.unit
class TestDensityScoreBellShape:
    def test_below_target_scales_linearly(self) -> None:
        # 50% of target density => 50 score (matches v6 behavior)
        assert calculate_density_score(1.25, 2.5) == pytest.approx(50.0)

    def test_at_target_is_100(self) -> None:
        assert calculate_density_score(2.5, 2.5) == pytest.approx(100.0)

    def test_above_target_is_penalized(self) -> None:
        # density = 3.08 with target 2.5: over by 0.232 of target
        # penalty = 0.232 * 50 = 11.6, score = 88.4
        score = calculate_density_score(3.08, 2.5)
        assert 87.0 < score < 89.0

    def test_over_density_floors_at_zero(self) -> None:
        # density = 10.0 with target 2.5 is catastrophically over
        assert calculate_density_score(10.0, 2.5) == 0.0

    def test_zero_target_density_returns_zero(self) -> None:
        assert calculate_density_score(1.0, 0.0) == 0.0


@pytest.mark.unit
class TestHubSkew:
    def test_trivial_graph_returns_one(self) -> None:
        assert calculate_hub_skew([], 0) == 1.0
        assert calculate_hub_skew([], 5) == 1.0

    def test_balanced_graph_near_one(self) -> None:
        # 4 entities, each with exactly 2 connections — flat distribution
        rels = [
            {"source": 0, "target": 1, "type": "x"},
            {"source": 1, "target": 2, "type": "x"},
            {"source": 2, "target": 3, "type": "x"},
            {"source": 3, "target": 0, "type": "x"},
        ]
        assert calculate_hub_skew(rels, 4) == pytest.approx(1.0)

    def test_hub_and_spoke_has_high_skew(self) -> None:
        # entity 0 is the hub, connected to all others; others have degree 1
        rels = [{"source": 0, "target": i, "type": "x"} for i in range(1, 11)]
        skew = calculate_hub_skew(rels, 11)
        # hub has degree 10, all others degree 1 → skew = 10.0
        assert skew == pytest.approx(10.0)

    def test_ignores_invalid_indices(self) -> None:
        rels = [
            {"source": 0, "target": 1, "type": "x"},
            {"source": 99, "target": 1, "type": "x"},  # out of range source
            {"source": 0, "target": -1, "type": "x"},  # out of range target
        ]
        skew = calculate_hub_skew(rels, 2)
        # only the first edge counts; both entities have degree 1
        assert skew == pytest.approx(1.0)


@pytest.mark.unit
class TestReciprocalRate:
    def test_empty_relationships(self) -> None:
        assert calculate_reciprocal_rate([]) == 0.0

    def test_no_reciprocals(self) -> None:
        rels = [
            {"source": 0, "target": 1, "type": "x"},
            {"source": 1, "target": 2, "type": "y"},
        ]
        assert calculate_reciprocal_rate(rels) == 0.0

    def test_full_reciprocals(self) -> None:
        # Both directions same type = both count as reciprocal
        rels = [
            {"source": 0, "target": 1, "type": "possesses"},
            {"source": 1, "target": 0, "type": "possesses"},
        ]
        assert calculate_reciprocal_rate(rels) == pytest.approx(1.0)

    def test_different_types_not_reciprocal(self) -> None:
        rels = [
            {"source": 0, "target": 1, "type": "serves"},
            {"source": 1, "target": 0, "type": "admires"},
        ]
        assert calculate_reciprocal_rate(rels) == 0.0


@pytest.mark.unit
class TestStructuralPenalty:
    def test_clean_graph_no_penalty(self) -> None:
        rels = [
            {"source": 0, "target": 1, "type": "x"},
            {"source": 1, "target": 2, "type": "y"},
            {"source": 2, "target": 3, "type": "z"},
        ]
        penalty, hub, recip = calculate_structural_penalty(rels, 4)
        assert penalty == 0.0
        assert recip == 0.0

    def test_small_graph_hub_skew_ignored(self) -> None:
        # Small graph with skewed hub — penalty suppressed because entity_count < 10
        rels = [{"source": 0, "target": i, "type": "x"} for i in range(1, 5)]
        penalty, hub, recip = calculate_structural_penalty(rels, 5)
        assert hub > 3.0
        # Under 10 entities, hub-skew penalty doesn't kick in
        assert penalty == 0.0

    def test_large_hub_triggers_penalty(self) -> None:
        rels = [{"source": 0, "target": i, "type": "x"} for i in range(1, 11)]
        penalty, hub, recip = calculate_structural_penalty(rels, 11)
        # skew = 10, penalty = (10-3) * 2 = 14 → capped at 10
        assert hub == pytest.approx(10.0)
        assert penalty == pytest.approx(10.0)

    def test_reciprocal_rate_triggers_penalty(self) -> None:
        # Full reciprocals (rate=1.0) → penalty = (1.0-0.10)*50 = 45 → capped at 10
        rels = [
            {"source": 0, "target": 1, "type": "t"},
            {"source": 1, "target": 0, "type": "t"},
        ]
        penalty, hub, recip = calculate_structural_penalty(rels, 2)
        assert recip == pytest.approx(1.0)
        assert penalty == pytest.approx(10.0)

    def test_combined_penalty_capped_at_15(self) -> None:
        # Hub-and-spoke with reciprocals everywhere
        rels = []
        for i in range(1, 11):
            rels.append({"source": 0, "target": i, "type": "x"})
            rels.append({"source": i, "target": 0, "type": "x"})
        penalty, hub, recip = calculate_structural_penalty(rels, 11)
        # Both max out at 10, sum 20, capped at STRUCTURAL_PENALTY_MAX=15
        assert penalty == pytest.approx(15.0)


@pytest.mark.unit
class TestCalculateQualityGradeV7:
    def test_empty_source(self) -> None:
        grade, label, *_ = calculate_quality_grade(
            avg_entity_quality=0.0,
            avg_relationship_quality=0.0,
            entity_count=0,
            relationships=[],
            connectivity_ratio=0.0,
            low_quality_entity_count=0,
            low_quality_relationship_count=0,
        )
        assert grade == 0.0
        assert label == "Low"

    def test_v7_weights_clean_graph(self) -> None:
        """R=80, E=70, T=60 with no penalties should apply v7 weights exactly."""
        # One-edge graph avoids structural penalties. 2 entities, 1 rel → density 0.5
        # We inject a fixed topology by crafting inputs: we'll just verify the
        # weighting by comparing to expected (R*0.50 + E*0.35 + T*0.15).
        rels = [{"source": 0, "target": 1, "type": "x"}]
        grade, label, density_ratio, density_score, topology, pollution, structural, hub, recip = (
            calculate_quality_grade(
                avg_entity_quality=70.0,
                avg_relationship_quality=80.0,
                entity_count=2,
                relationships=rels,
                connectivity_ratio=1.0,
                low_quality_entity_count=0,
                low_quality_relationship_count=0,
                target_density=DEFAULT_TARGET_DENSITY,
            )
        )
        assert pollution == 0.0
        assert structural == 0.0
        assert hub == pytest.approx(1.0)
        assert recip == 0.0
        expected_density = calculate_density_score(0.5, DEFAULT_TARGET_DENSITY)
        expected_topology = (100.0 + expected_density) / 2
        expected_grade = 80.0 * 0.50 + 70.0 * 0.35 + expected_topology * 0.15
        assert grade == pytest.approx(expected_grade, abs=0.05)
        assert density_ratio == pytest.approx(0.5)
        assert topology == pytest.approx(expected_topology)

    def test_entity_only_uses_fallback_weights(self) -> None:
        grade, *_ = calculate_quality_grade(
            avg_entity_quality=80.0,
            avg_relationship_quality=0.0,
            entity_count=5,
            relationships=[],
            connectivity_ratio=0.0,
            low_quality_entity_count=0,
            low_quality_relationship_count=0,
        )
        # E*0.55 + T*0.45 with T=0 → 80*0.55 = 44.0
        assert grade == pytest.approx(44.0, abs=0.01)

    def test_over_dense_graph_loses_density_points(self) -> None:
        """A graph with 3x target density gets a reduced density score."""
        # 10 entities, 75 relationships → density 7.5 (3x over 2.5)
        rels = [{"source": i % 10, "target": (i + 1) % 10, "type": f"t{i % 5}"} for i in range(75)]
        _, _, density_ratio, density_score, *_ = calculate_quality_grade(
            avg_entity_quality=50.0,
            avg_relationship_quality=50.0,
            entity_count=10,
            relationships=rels,
            connectivity_ratio=1.0,
            low_quality_entity_count=0,
            low_quality_relationship_count=0,
        )
        assert density_ratio == pytest.approx(7.5)
        # 7.5 is 2.0x over target (excess=5.0, target=2.5 → excess_frac=2.0)
        # density_score = 100 - 2.0*50 = 0 (floored)
        assert density_score == 0.0

    def test_hub_and_reciprocal_reduce_grade(self) -> None:
        """A hub-and-spoke graph with reciprocal bloat should be penalized."""
        rels = []
        for i in range(1, 11):
            rels.append({"source": 0, "target": i, "type": "t"})
            rels.append({"source": i, "target": 0, "type": "t"})
        grade, _, _, _, _, pollution, structural, hub, recip = calculate_quality_grade(
            avg_entity_quality=90.0,
            avg_relationship_quality=90.0,
            entity_count=11,
            relationships=rels,
            connectivity_ratio=1.0,
            low_quality_entity_count=0,
            low_quality_relationship_count=0,
        )
        assert pollution == 0.0
        assert structural > 0
        assert hub > 3.0
        assert recip > 0.5
        # Grade should be meaningfully reduced compared to the same metrics with
        # no structural penalty — a clean-graph version scoring the same averages
        # would produce ~90*0.5 + 90*0.35 + T*0.15 = ~80+
        # Here structural penalty pulls it down at least 10 points
        assert grade < 80.0
