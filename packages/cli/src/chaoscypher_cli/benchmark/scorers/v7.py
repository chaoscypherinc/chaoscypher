# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""V7 extraction scorer - wraps chaoscypher_core.services.quality.QualityScorer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_cli.benchmark.results import ScoreResult
from chaoscypher_core.services.quality import (
    SCORING_VERSION,
    QualityScorer,
)


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.dataset import RawOutput


class V7ExtractionScorer:
    """Score a dataset's RawOutput using the v7 quality scorer.

    The headline_score is the v7 quality_grade (0-100). The metrics dict
    carries the full v7 breakdown for downstream display.
    """

    version: int = SCORING_VERSION

    def __init__(self, quality_config: dict[str, Any] | None = None) -> None:
        """Build the underlying QualityScorer with optional config overrides."""
        self._scorer = QualityScorer(quality_config)

    def score(self, output: RawOutput, fixture: Any) -> ScoreResult:
        """Score one RawOutput.

        Args:
            output: Pack output to score.
            fixture: Unused for extraction; reserved for chat (v2 will pass
                a graph + Q&A fixture here).
        """
        del fixture  # unused for extraction
        source_score = self._scorer.score_source(
            source_id="benchmark",
            entities=output.entities,
            relationships=output.relationships,
        )
        return ScoreResult(
            headline_score=source_score.quality_grade,
            metrics={
                "avg_entity_quality": source_score.avg_entity_quality,
                "avg_relationship_quality": source_score.avg_relationship_quality,
                "topology_score": source_score.topology_score,
                "density_ratio": source_score.density_ratio,
                "density_score": source_score.density_score,
                "connectivity_ratio": source_score.connectivity_ratio,
                "pollution_penalty": source_score.pollution_penalty,
                "structural_penalty": source_score.structural_penalty,
                "hub_skew": source_score.hub_skew,
                "reciprocal_rate": source_score.reciprocal_rate,
                "entity_count": source_score.entity_count,
                "relationship_count": source_score.relationship_count,
                "low_quality_entity_count": source_score.low_quality_entity_count,
                "low_quality_relationship_count": source_score.low_quality_relationship_count,
                "quality_label": source_score.quality_label,
            },
        )


__all__ = ["V7ExtractionScorer"]
