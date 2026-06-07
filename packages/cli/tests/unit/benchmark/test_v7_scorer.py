# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for V7ExtractionScorer."""

from __future__ import annotations

from chaoscypher_cli.benchmark.dataset import RawOutput
from chaoscypher_cli.benchmark.scorers.v7 import V7ExtractionScorer


def _make_output(entity_count: int, rel_count: int) -> RawOutput:
    """Build a RawOutput with synthetic well-formed entities/relationships."""
    entities = [
        {
            "name": f"Entity{i}",
            "type": "person",
            "description": "A reasonably descriptive bio of the entity going on for several words.",
            "confidence": 0.9,
            "properties": {"role": "main", "era": "1800s"},
            "aliases": [f"E{i}"],
        }
        for i in range(entity_count)
    ]
    relationships = [
        {
            "type": "knows",
            "source": i % entity_count if entity_count else 0,
            "target": (i + 1) % entity_count if entity_count else 0,
            "justification": "They appear together in multiple scenes throughout the document.",
            "confidence": 0.85,
        }
        for i in range(rel_count)
    ]
    return RawOutput(
        entities=entities,
        relationships=relationships,
        latency_ms=100,
        input_tokens=10,
        output_tokens=20,
        error=None,
    )


def test_v7_scorer_returns_score_in_zero_to_hundred():
    scorer = V7ExtractionScorer()
    out = _make_output(entity_count=10, rel_count=15)
    result = scorer.score(out, fixture=None)
    assert 0.0 <= result.headline_score <= 100.0


def test_v7_scorer_metrics_carry_breakdown():
    scorer = V7ExtractionScorer()
    out = _make_output(entity_count=10, rel_count=15)
    result = scorer.score(out, fixture=None)
    assert "avg_entity_quality" in result.metrics
    assert "avg_relationship_quality" in result.metrics
    assert "topology_score" in result.metrics
    assert "pollution_penalty" in result.metrics
    assert "structural_penalty" in result.metrics


def test_v7_scorer_empty_extraction_grades_zero():
    scorer = V7ExtractionScorer()
    out = _make_output(entity_count=0, rel_count=0)
    result = scorer.score(out, fixture=None)
    assert result.headline_score == 0.0


def test_v7_scorer_version_matches_core_constant():
    from chaoscypher_core.services.quality import SCORING_VERSION

    scorer = V7ExtractionScorer()
    assert scorer.version == SCORING_VERSION
