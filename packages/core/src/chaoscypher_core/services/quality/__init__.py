# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality Scoring Service.

Provides extraction quality scoring to evaluate and compare extraction
quality across sources, enabling identification of which extraction
approaches produce the best results.

The scoring system evaluates:
- Entity quality (description, confidence, cross-chunk, properties, aliases, type value)
- Relationship quality (justification, confidence, specificity, valid references)
- Overall source quality (sum of entity and relationship contributions + connectivity bonus)
"""

from chaoscypher_core.services.quality.scoring import (
    SCORING_VERSION,
    EntityQualityScore,
    QualityScorer,
    RelationshipQualityScore,
    SourceQualityScore,
    calculate_entity_score,
    calculate_quality_grade,
    calculate_relationship_score,
    calculate_source_score,
)


__all__ = [
    "SCORING_VERSION",
    "EntityQualityScore",
    "QualityScorer",
    "RelationshipQualityScore",
    "SourceQualityScore",
    "calculate_entity_score",
    "calculate_quality_grade",
    "calculate_relationship_score",
    "calculate_source_score",
]
