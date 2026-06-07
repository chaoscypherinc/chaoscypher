# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality Scoring Functions.

Implements the extraction quality scoring formula v7:

Entity Quality Score (per entity) - 0-100:
- Description: 250+ chars → 20, 150+ → 17, 100+ → 14, 50+ → 10, 20+ → 6, else → 3
- Confidence: clamp(confidence, 0-1) * 15
- Cross-chunk: 5+ chunks → 15, 3+ → 12, 2 → 8, 1 → 7
- Properties: 7+ props → 15, 5+ → 13, 3+ → 10, 2 → 7, 1 → 4, 0 → 0
- Aliases: 3+ aliases → 10, 2 → 8, 1 → 5, 0 → 0
- Type Value: From domain template quality_score (default=18)

Relationship Quality Score (per relationship) - 0-100:
- Justification: 50+ chars → 35, 40+ → 30, 25+ → 22, 10+ → 15, else → 5
- Confidence: clamp(confidence, 0-1) * 25
- Specificity: From domain template quality_score (default=15)
- Valid refs: Both valid → 15, one valid → 8, neither → 0

Source Quality Score (uncapped, quality-weighted):
- Entity contribution: Sum of quality-weighted entity scores
  (each entity contributes: score * (score/100) to penalize low-quality inflation)
- Relationship contribution: Sum of quality-weighted relationship scores
- Connectivity bonus: connected_entities * 10

Coverage Score (0-100):
- Formula: min(100, (entity_count / chunk_count) * 100)
- Measures extraction completeness as entities per chunk, normalized to 0-100.

Final Grade Calculation (v7 weighted formula):
- When relationships exist:
  Weighted Sum = (R * 0.50) + (E * 0.35) + (T * 0.15)
- When NO relationships exist (entity-only sources):
  Weighted Sum = (E * 0.55) + (T * 0.45)
  where:
  - R = Relationship Quality (avg_relationship_quality, 0-100)
  - E = Entity Quality (avg_entity_quality, 0-100)
  - T = Topology Score (0-100)
    - T = (Connectivity Score + Density Score) / 2
    - Connectivity Score = connectivity_ratio * 100
    - Density Score: bell-shaped around target density
      - If Actual ≤ Target: (Actual / Target) * 100 (unchanged from v6)
      - If Actual > Target: 100 - ((Actual - Target) / Target) * 50, floored at 0
        (over-dense graphs are penalized — a model padding edges is not rewarded)
- Pollution Penalty (0-15): For every 10% of items with score < 40, deduct 5.
- Structural Penalty (0-15): Graph-structural noise signals:
  - Hub Skew: max_entity_degree / median_entity_degree (only counts when
    entity_count ≥ 10). When ratio > 3, penalty grows: min(10, (ratio-3)*2).
    Catches one entity being over-connected to everything else.
  - Reciprocal Rate: fraction of (src,tgt,type) edges whose (tgt,src,type)
    also exists. When rate > 10%, penalty grows: min(10, (rate-0.10)*50).
    Catches symmetric duplicates / directional errors.
  - Combined: hub_skew_penalty + reciprocal_penalty, capped at 15.
- Final Grade = MAX(0, Weighted Sum - Pollution Penalty - Structural Penalty)

Quality Labels:
- Outstanding: grade >= 85
- Excellent: grade >= 70
- Good: grade >= 50
- Fair: grade >= 30
- Low: grade < 30
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog


logger = structlog.get_logger(__name__)

# Scoring algorithm version - bump this when the scoring formula changes
# This triggers automatic recalculation of cached scores on startup
SCORING_VERSION = 7


# Default scores when domain/template doesn't specify
DEFAULT_ENTITY_SCORE = 18
DEFAULT_RELATIONSHIP_SCORE = 15

# Target density for a "healthy" graph (edges per node)
# Domains can override this in their quality_scoring config
DEFAULT_TARGET_DENSITY = 2.5

# Over-density penalty rate: how steeply to penalize density beyond target.
# At `Actual = 2 * Target`, density_score drops to 50.
OVER_DENSITY_PENALTY_RATE = 50.0

# Hub-skew penalty thresholds (see calculate_structural_penalty).
HUB_SKEW_MIN_ENTITIES = 10
HUB_SKEW_RATIO_THRESHOLD = 3.0
HUB_SKEW_PENALTY_PER_UNIT = 2.0
HUB_SKEW_MAX_PENALTY = 10.0

# Reciprocal-rate penalty thresholds.
RECIPROCAL_RATE_THRESHOLD = 0.10
RECIPROCAL_RATE_PENALTY_PER_UNIT = 50.0
RECIPROCAL_RATE_MAX_PENALTY = 10.0

# Total structural penalty cap (independent of pollution_penalty).
STRUCTURAL_PENALTY_MAX = 15.0


# ---------------------------------------------------------------------------
# Threshold lookup tables for data-driven scoring
# Each tuple is (threshold, score) in descending order.
# ---------------------------------------------------------------------------

_DESCRIPTION_THRESHOLDS: Sequence[tuple[float, float]] = [
    (250, 20.0),
    (150, 17.0),
    (100, 14.0),
    (50, 10.0),
    (20, 6.0),
]

_CROSS_CHUNK_THRESHOLDS: Sequence[tuple[float, float]] = [
    (5, 15.0),
    (3, 12.0),
    (2, 8.0),
]

_PROPERTIES_THRESHOLDS: Sequence[tuple[float, float]] = [
    (7, 15.0),
    (5, 13.0),
    (3, 10.0),
    (2, 7.0),
    (1, 4.0),
]

_ALIASES_THRESHOLDS: Sequence[tuple[float, float]] = [
    (3, 10.0),
    (2, 8.0),
    (1, 5.0),
]

_JUSTIFICATION_THRESHOLDS: Sequence[tuple[float, float]] = [
    (50, 35.0),
    (40, 30.0),
    (25, 22.0),
    (10, 15.0),
]

_GRADE_LABEL_THRESHOLDS: Sequence[tuple[float, str]] = [
    (85, "Outstanding"),
    (70, "Excellent"),
    (50, "Good"),
    (30, "Fair"),
]


def _score_by_threshold(
    value: float,
    thresholds: Sequence[tuple[float, float]],
    default: float = 0.0,
) -> float:
    """Return score for value by matching against descending thresholds.

    Iterates through ``(threshold, score)`` pairs and returns the score
    for the first threshold that ``value`` meets or exceeds.

    Args:
        value: The numeric value to evaluate.
        thresholds: Sequence of ``(threshold, score)`` pairs in descending order.
        default: Score returned when no threshold is met.

    Returns:
        The matched score, or *default* if no threshold matched.
    """
    for threshold, score in thresholds:
        if value >= threshold:
            return score
    return default


def _label_by_threshold(value: float, thresholds: Sequence[tuple[float, str]], default: str) -> str:
    """Return label for value by matching against descending thresholds.

    Same logic as :func:`_score_by_threshold` but returns a string label.

    Args:
        value: The numeric value to evaluate.
        thresholds: Sequence of ``(threshold, label)`` pairs in descending order.
        default: Label returned when no threshold is met.

    Returns:
        The matched label, or *default* if no threshold matched.
    """
    for threshold, label in thresholds:
        if value >= threshold:
            return label
    return default


def calculate_density_score(density_ratio: float, target_density: float) -> float:
    """Return the v7 bell-shaped density score.

    - For ``density_ratio <= target_density``: linear from 0 to 100
      (same as the v6 under-density curve).
    - For ``density_ratio > target_density``: deducts
      ``OVER_DENSITY_PENALTY_RATE`` points per 1.0x-of-target excess, floored at 0.
      This stops rewarding models that pad the graph with excess edges.

    Args:
        density_ratio: Relationships per entity.
        target_density: Target ratio for a "healthy" graph.

    Returns:
        Density score in [0, 100].
    """
    if target_density <= 0:
        return 0.0
    if density_ratio <= target_density:
        return min(100.0, (density_ratio / target_density) * 100.0)
    over_fraction = (density_ratio - target_density) / target_density
    return max(0.0, 100.0 - over_fraction * OVER_DENSITY_PENALTY_RATE)


def _median(values: Sequence[float]) -> float:
    """Return the median of a non-empty sequence of numbers."""
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def calculate_hub_skew(
    relationships: Sequence[dict[str, Any]],
    entity_count: int,
) -> float:
    """Return max_entity_degree / median_entity_degree across all entities.

    Only entities that appear in at least one relationship contribute to the
    median (isolated entities would otherwise flatten the median to zero for
    sparse graphs). Returns 1.0 when the graph is too small or trivial.

    A high value means one entity is connected to many others while most are
    not — a pattern the LLM produces when it anchors hallucinated edges on a
    memorable entity.

    Args:
        relationships: List of relationship dicts with integer ``source``/``target``.
        entity_count: Total entities (for index validation).

    Returns:
        Skew ratio (≥ 1.0). Returns 1.0 for trivial graphs.
    """
    if entity_count <= 0 or not relationships:
        return 1.0

    degree: dict[int, int] = {}
    for rel in relationships:
        for idx in (rel.get("source"), rel.get("target")):
            if isinstance(idx, int) and 0 <= idx < entity_count:
                degree[idx] = degree.get(idx, 0) + 1

    if not degree:
        return 1.0

    degrees = list(degree.values())
    max_d = max(degrees)
    med_d = _median([float(d) for d in degrees])
    if med_d <= 0:
        return 1.0
    return max_d / med_d


def calculate_reciprocal_rate(
    relationships: Sequence[dict[str, Any]],
) -> float:
    """Return the fraction of edges that have a same-type reciprocal partner.

    For each edge ``(src, tgt, type)``, counts it as reciprocal if
    ``(tgt, src, type)`` also exists in the set. Both sides of the pair count,
    so a single symmetric pair contributes 2/N to the rate.

    Catches:
    - Directional errors (``A possesses B`` + ``B possesses A`` — one is wrong)
    - Redundant same-type pairs the dedup stage didn't collapse

    Args:
        relationships: List of relationship dicts.

    Returns:
        Rate in [0, 1].
    """
    if not relationships:
        return 0.0

    edges: set[tuple[int, int, str]] = set()
    for rel in relationships:
        s = rel.get("source")
        t = rel.get("target")
        ty = rel.get("type") or ""
        if isinstance(s, int) and isinstance(t, int):
            edges.add((s, t, ty))

    if not edges:
        return 0.0

    reciprocal = sum(1 for (s, t, ty) in edges if (t, s, ty) in edges and s != t)
    return reciprocal / len(edges)


def calculate_structural_penalty(
    relationships: Sequence[dict[str, Any]],
    entity_count: int,
) -> tuple[float, float, float]:
    """Compute the v7 structural penalty plus the raw signals that drove it.

    Args:
        relationships: Extracted relationships.
        entity_count: Number of entities.

    Returns:
        Tuple of ``(structural_penalty, hub_skew, reciprocal_rate)`` where
        ``structural_penalty`` is capped at :data:`STRUCTURAL_PENALTY_MAX`.
    """
    hub_skew = calculate_hub_skew(relationships, entity_count)
    reciprocal_rate = calculate_reciprocal_rate(relationships)

    hub_penalty = 0.0
    if entity_count >= HUB_SKEW_MIN_ENTITIES and hub_skew > HUB_SKEW_RATIO_THRESHOLD:
        hub_penalty = min(
            HUB_SKEW_MAX_PENALTY,
            (hub_skew - HUB_SKEW_RATIO_THRESHOLD) * HUB_SKEW_PENALTY_PER_UNIT,
        )

    reciprocal_penalty = 0.0
    if reciprocal_rate > RECIPROCAL_RATE_THRESHOLD:
        reciprocal_penalty = min(
            RECIPROCAL_RATE_MAX_PENALTY,
            (reciprocal_rate - RECIPROCAL_RATE_THRESHOLD) * RECIPROCAL_RATE_PENALTY_PER_UNIT,
        )

    total = min(STRUCTURAL_PENALTY_MAX, hub_penalty + reciprocal_penalty)
    return total, hub_skew, reciprocal_rate


def calculate_pollution_penalty(
    low_quality_entity_count: int,
    low_quality_relationship_count: int,
    entity_count: int,
    relationship_count: int,
) -> float:
    """Calculate pollution penalty based on percentage of low-quality items.

    For every 10% of items with score < 40, deduct 5 points (capped at 15).

    Args:
        low_quality_entity_count: Number of entities with score < 40.
        low_quality_relationship_count: Number of relationships with score < 40.
        entity_count: Total number of entities.
        relationship_count: Total number of relationships.

    Returns:
        Penalty value between 0 and 15.
    """
    total_items = entity_count + relationship_count
    if total_items == 0:
        return 0.0

    low_quality_count = low_quality_entity_count + low_quality_relationship_count
    # Explicit float division to avoid integer truncation
    low_quality_percentage = (float(low_quality_count) / float(total_items)) * 100.0

    # Deduct 5 points for every full 10% of low-quality items
    # e.g., 9.99% -> 0 penalty, 10.0% -> 5 penalty, 19.99% -> 5 penalty, 20.0% -> 10 penalty
    penalty_tiers = int(low_quality_percentage // 10.0)
    penalty = float(penalty_tiers * 5)
    return min(15.0, penalty)


def calculate_quality_grade(
    avg_entity_quality: float,
    avg_relationship_quality: float,
    entity_count: int,
    relationships: Sequence[dict[str, Any]],
    connectivity_ratio: float,
    low_quality_entity_count: int,
    low_quality_relationship_count: int,
    target_density: float = DEFAULT_TARGET_DENSITY,
) -> tuple[float, str, float, float, float, float, float, float, float]:
    """Calculate quality grade using the v7 weighted component formula.

    The quality grade measures extraction quality using a weighted formula:
    - When relationships exist:
      Weighted Sum = (R * 0.50) + (E * 0.35) + (T * 0.15)
    - When NO relationships (entity-only sources):
      Weighted Sum = (E * 0.55) + (T * 0.45)
    - Final Grade = MAX(0, Weighted Sum - Pollution Penalty - Structural Penalty)

    Where:
    - R = Relationship Quality (avg_relationship_quality, 0-100)
    - E = Entity Quality (avg_entity_quality, 0-100)
    - T = Topology Score (0-100)
      - T = (Connectivity Score + Density Score) / 2
      - Connectivity Score = connectivity_ratio * 100
      - Density Score = bell-shaped around target density (see
        :func:`calculate_density_score`) — over-dense graphs are penalized.
    - Structural Penalty (0-15) combines hub-skew and reciprocal-rate signals
      (see :func:`calculate_structural_penalty`) to detect padded / bloated graphs.

    Args:
        avg_entity_quality: Average quality score per entity (0-100).
        avg_relationship_quality: Average quality score per relationship (0-100).
        entity_count: Total number of entities.
        relationships: Extracted relationship dicts (used for structural signals).
        connectivity_ratio: Ratio of connected entities (0-1).
        low_quality_entity_count: Number of entities with score < 40.
        low_quality_relationship_count: Number of relationships with score < 40.
        target_density: Target ratio of relationships to entities (default: 2.5).

    Returns:
        Tuple of (quality_grade, quality_label, density_ratio, density_score,
                  topology_score, pollution_penalty, structural_penalty,
                  hub_skew, reciprocal_rate).
    """
    relationship_count = len(relationships)

    # No entities = no grade
    if entity_count == 0:
        return 0.0, "Low", 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0

    # Calculate density metrics (bell-shaped in v7 — over-density is penalized)
    density_ratio = relationship_count / entity_count
    density_score = calculate_density_score(density_ratio, target_density)

    # Calculate connectivity score (0-100)
    connectivity_score = connectivity_ratio * 100

    # Topology = average of connectivity and density
    topology_score = (connectivity_score + density_score) / 2

    # Calculate pollution penalty (low-quality item inflation)
    pollution_penalty = calculate_pollution_penalty(
        low_quality_entity_count,
        low_quality_relationship_count,
        entity_count,
        relationship_count,
    )

    # Calculate structural penalty (graph-shape noise signals)
    structural_penalty, hub_skew, reciprocal_rate = calculate_structural_penalty(
        relationships, entity_count
    )

    # Adaptive weighting: when relationships exist, weight them at 50%;
    # when entity-only, redistribute to avoid capping grade at ~50
    if relationship_count > 0:
        weighted_sum = (
            (avg_relationship_quality * 0.50)
            + (avg_entity_quality * 0.35)
            + (topology_score * 0.15)
        )
    else:
        # Entity-only sources (directories, glossaries, entity lists)
        weighted_sum = (avg_entity_quality * 0.55) + (topology_score * 0.45)

    # Final grade = weighted sum - pollution - structural, clamped to 0-100
    grade = max(0.0, min(100.0, weighted_sum - pollution_penalty - structural_penalty))

    # Determine label based on grade
    label = _label_by_threshold(grade, _GRADE_LABEL_THRESHOLDS, default="Low")

    return (
        grade,
        label,
        density_ratio,
        density_score,
        topology_score,
        pollution_penalty,
        structural_penalty,
        hub_skew,
        reciprocal_rate,
    )


@dataclass
class EntityQualityScore:
    """Quality score breakdown for a single entity.

    Attributes:
        entity_name: Name of the entity.
        entity_type: Type of the entity.
        description_score: Score for description richness (0-20).
        confidence_score: Score for extraction confidence (0-15).
        cross_chunk_score: Score for cross-chunk mentions (0-15).
        properties_score: Score for property richness (0-15).
        aliases_score: Score for alias count (0-10).
        type_value_score: Score based on entity type tier (0-25).
        total_score: Sum of all component scores (0-100).
    """

    entity_name: str
    entity_type: str
    description_score: float
    confidence_score: float
    cross_chunk_score: float
    properties_score: float
    aliases_score: float
    type_value_score: float
    total_score: float = field(init=False)

    def __post_init__(self) -> None:
        """Calculate total score after initialization."""
        self.total_score = min(
            100.0,
            self.description_score
            + self.confidence_score
            + self.cross_chunk_score
            + self.properties_score
            + self.aliases_score
            + self.type_value_score,
        )


@dataclass
class RelationshipQualityScore:
    """Quality score breakdown for a single relationship.

    Attributes:
        relationship_type: Type of the relationship.
        source_entity: Name of source entity.
        target_entity: Name of target entity.
        justification_score: Score for justification richness (0-35).
        confidence_score: Score for extraction confidence (0-25).
        specificity_score: Score based on relationship type tier (0-25).
        valid_refs_score: Score for valid entity references (0/8/15).
        total_score: Sum of all component scores (0-100).
    """

    relationship_type: str
    source_entity: str
    target_entity: str
    justification_score: float
    confidence_score: float
    specificity_score: float
    valid_refs_score: float
    total_score: float = field(init=False)

    def __post_init__(self) -> None:
        """Calculate total score after initialization."""
        self.total_score = min(
            100.0,
            self.justification_score
            + self.confidence_score
            + self.specificity_score
            + self.valid_refs_score,
        )


@dataclass
class SourceQualityScore:
    """Quality score breakdown for an entire source.

    Attributes:
        source_id: ID of the source.
        entity_count: Number of entities.
        relationship_count: Number of relationships.
        entity_contribution: Sum of quality-weighted entity scores.
        relationship_contribution: Sum of quality-weighted relationship scores.
        connectivity_bonus: Bonus for connected entities.
        total_score: Sum of contributions and bonus (richness score, unbounded).
        avg_entity_quality: Average quality per entity.
        avg_relationship_quality: Average quality per relationship.
        connectivity_ratio: Ratio of connected entities.
        quality_grade: Quality rating on 0-100 scale (independent of volume).
        quality_label: Human-readable quality label (Outstanding/Excellent/Good/Fair/Low).
        low_quality_entity_count: Entities with score < 40 (inflation indicator).
        low_quality_relationship_count: Relationships with score < 40.
        density_ratio: Relationships per entity ratio.
        density_score: Density score normalized to 0-100 (bell-shaped, v7+).
        topology_score: Combined connectivity + density score (0-100).
        pollution_penalty: Penalty for low-quality items (0-15).
        structural_penalty: Penalty for graph-shape noise signals (0-15, v7+).
        hub_skew: max_entity_degree / median_entity_degree (v7+).
        reciprocal_rate: Fraction of edges with a same-type reciprocal (v7+).
        coverage_score: Entities per chunk normalized to 0-100.
        entity_scores: Individual entity score breakdowns.
        relationship_scores: Individual relationship score breakdowns.
    """

    source_id: str
    entity_count: int
    relationship_count: int
    entity_contribution: float
    relationship_contribution: float
    connectivity_bonus: float
    total_score: float
    avg_entity_quality: float
    avg_relationship_quality: float
    connectivity_ratio: float
    quality_grade: float = 0.0
    quality_label: str = "Low"
    low_quality_entity_count: int = 0
    low_quality_relationship_count: int = 0
    density_ratio: float = 0.0
    density_score: float = 0.0
    topology_score: float = 0.0
    pollution_penalty: float = 0.0
    structural_penalty: float = 0.0
    hub_skew: float = 1.0
    reciprocal_rate: float = 0.0
    coverage_score: float = 0.0
    entity_scores: list[EntityQualityScore] = field(default_factory=list)
    relationship_scores: list[RelationshipQualityScore] = field(default_factory=list)


class QualityScorer:
    """Scorer for extraction quality evaluation.

    Uses domain-specific configuration to assign appropriate scores
    to entity and relationship types.

    Attributes:
        quality_config: Domain quality scoring configuration.
        entity_scores: Direct type-name to score mapping for entities.
        relationship_scores: Direct type-name to score mapping for relationships.
        default_entity_score: Fallback score for unknown entity types.
        default_relationship_score: Fallback score for unknown relationship types.
        target_density: Target relationships per entity ratio for a "healthy" graph.
    """

    def __init__(self, quality_config: dict[str, Any] | None = None) -> None:
        """Initialize scorer with domain quality configuration.

        Args:
            quality_config: Domain quality_scoring configuration dict.
                Expected keys: ``entity_scores``, ``relationship_scores``,
                ``default_entity_score``, ``default_relationship_score``.
                If None, uses default scoring.
        """
        self.quality_config = quality_config or {}
        self.target_density = float(
            self.quality_config.get("target_density", DEFAULT_TARGET_DENSITY)
        )
        self._build_type_mappings()

    def _build_type_mappings(self) -> None:
        """Build direct type-to-score mappings from configuration."""
        self.entity_scores: dict[str, float] = {
            k: float(v) for k, v in self.quality_config.get("entity_scores", {}).items()
        }
        self.relationship_scores: dict[str, float] = {
            k: float(v) for k, v in self.quality_config.get("relationship_scores", {}).items()
        }
        self.default_entity_score = float(
            self.quality_config.get("default_entity_score", DEFAULT_ENTITY_SCORE)
        )
        self.default_relationship_score = float(
            self.quality_config.get("default_relationship_score", DEFAULT_RELATIONSHIP_SCORE)
        )

    def get_entity_type_score(self, entity_type: str) -> float:
        """Get score for an entity type.

        Args:
            entity_type: The entity type name.

        Returns:
            Score for this entity type (typically 10-25).
        """
        score = self.entity_scores.get(entity_type)
        if score is not None:
            return score
        if entity_type:
            logger.debug(
                "unknown_entity_type_fallback",
                entity_type=entity_type,
                default_score=self.default_entity_score,
            )
        return self.default_entity_score

    def get_relationship_type_score(self, relationship_type: str) -> float:
        """Get score for a relationship type.

        Args:
            relationship_type: The relationship type name.

        Returns:
            Score for this relationship type (typically 5-25).
        """
        score = self.relationship_scores.get(relationship_type)
        if score is not None:
            return score
        if relationship_type:
            logger.debug(
                "unknown_relationship_type_fallback",
                relationship_type=relationship_type,
                default_score=self.default_relationship_score,
            )
        return self.default_relationship_score

    def score_entity(self, entity: dict[str, Any], chunk_mentions: int = 1) -> EntityQualityScore:
        """Score a single entity.

        Args:
            entity: Entity dict with name, type, description, confidence, properties, aliases.
            chunk_mentions: Number of chunks this entity appears in.

        Returns:
            EntityQualityScore with breakdown.
        """
        name = entity.get("name", "Unknown")
        entity_type = entity.get("type", "")
        description = entity.get("description", "")
        confidence = entity.get("confidence", 0.0)
        properties = entity.get("properties", {}) or {}
        aliases = entity.get("aliases", []) or []

        # Description score (0-20)
        desc_len = len(description)
        description_score = _score_by_threshold(desc_len, _DESCRIPTION_THRESHOLDS, default=3.0)

        # Confidence score (0-15) — clamp to [0, 1] as defense-in-depth
        clamped_confidence = max(0.0, min(1.0, float(confidence)))
        confidence_score = clamped_confidence * 15.0

        # Cross-chunk score (0-15)
        cross_chunk_score = _score_by_threshold(
            chunk_mentions, _CROSS_CHUNK_THRESHOLDS, default=7.0
        )

        # Properties score (0-15)
        prop_count = len(properties)
        properties_score = _score_by_threshold(prop_count, _PROPERTIES_THRESHOLDS, default=0.0)

        # Aliases score (0-10)
        alias_count = len(aliases)
        aliases_score = _score_by_threshold(alias_count, _ALIASES_THRESHOLDS, default=0.0)

        # Type value score (0-25)
        type_value_score = self.get_entity_type_score(entity_type)

        return EntityQualityScore(
            entity_name=name,
            entity_type=entity_type,
            description_score=description_score,
            confidence_score=confidence_score,
            cross_chunk_score=cross_chunk_score,
            properties_score=properties_score,
            aliases_score=aliases_score,
            type_value_score=type_value_score,
        )

    def score_relationship(
        self,
        relationship: dict[str, Any],
        entity_count: int,
        entities: list[dict[str, Any]] | None = None,
    ) -> RelationshipQualityScore:
        """Score a single relationship.

        Args:
            relationship: Relationship dict with type, source, target, justification, confidence.
            entity_count: Total number of entities (for validating indices).
            entities: Optional list of entities (for getting names).

        Returns:
            RelationshipQualityScore with breakdown.
        """
        rel_type = relationship.get("type", "")
        source_idx = relationship.get("source")
        target_idx = relationship.get("target")
        justification = relationship.get("justification", "")
        confidence = relationship.get("confidence", 0.0)

        # Get entity names if entities provided
        source_name = "Unknown"
        target_name = "Unknown"
        if entities:
            if isinstance(source_idx, int) and 0 <= source_idx < len(entities):
                source_name = entities[source_idx].get("name", "Unknown")
            if isinstance(target_idx, int) and 0 <= target_idx < len(entities):
                target_name = entities[target_idx].get("name", "Unknown")

        # Justification score (0-35)
        just_len = len(justification)
        justification_score = _score_by_threshold(just_len, _JUSTIFICATION_THRESHOLDS, default=5.0)

        # Confidence score (0-25) — clamp to [0, 1] as defense-in-depth
        clamped_confidence = max(0.0, min(1.0, float(confidence)))
        confidence_score = clamped_confidence * 25.0

        # Specificity score (0-25)
        specificity_score = self.get_relationship_type_score(rel_type)

        # Valid refs score (0-15) — partial credit for one valid reference
        source_valid = isinstance(source_idx, int) and 0 <= source_idx < entity_count
        target_valid = isinstance(target_idx, int) and 0 <= target_idx < entity_count
        if source_valid and target_valid:
            valid_refs_score = 15.0
        elif source_valid or target_valid:
            valid_refs_score = 8.0
        else:
            valid_refs_score = 0.0

        return RelationshipQualityScore(
            relationship_type=rel_type,
            source_entity=source_name,
            target_entity=target_name,
            justification_score=justification_score,
            confidence_score=confidence_score,
            specificity_score=specificity_score,
            valid_refs_score=valid_refs_score,
        )

    def score_source(
        self,
        source_id: str,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        entity_chunk_mentions: dict[int, int] | None = None,
        chunk_count: int = 0,
    ) -> SourceQualityScore:
        """Score an entire source's extraction.

        Uses quality-weighted contributions to penalize low-quality inflation.
        Each item contributes: score * (score/100), so a 40-quality entity
        contributes 16 points while an 80-quality entity contributes 64 points.

        Args:
            source_id: Unique identifier for the source.
            entities: List of extracted entities.
            relationships: List of extracted relationships.
            entity_chunk_mentions: Optional mapping of entity index to chunk count.
            chunk_count: Total number of chunks in the source document (for coverage score).

        Returns:
            SourceQualityScore with full breakdown.
        """
        entity_chunk_mentions = entity_chunk_mentions or {}

        # Score all entities
        entity_scores: list[EntityQualityScore] = []
        for idx, entity in enumerate(entities):
            chunk_mentions = entity_chunk_mentions.get(idx, 1)
            score = self.score_entity(entity, chunk_mentions)
            entity_scores.append(score)

        # Score all relationships
        relationship_scores: list[RelationshipQualityScore] = []
        entity_count = len(entities)
        for relationship in relationships:
            rel_score = self.score_relationship(relationship, entity_count, entities)
            relationship_scores.append(rel_score)

        # Calculate quality-weighted contributions
        # score * (score/100) penalizes low-quality items:
        # - 80 quality: 80 * 0.8 = 64 contribution
        # - 40 quality: 40 * 0.4 = 16 contribution
        entity_contribution = sum(s.total_score * (s.total_score / 100.0) for s in entity_scores)
        relationship_contribution = sum(
            s.total_score * (s.total_score / 100.0) for s in relationship_scores
        )

        # Count low-quality items (score < 40) as inflation indicator
        low_quality_entity_count = sum(1 for s in entity_scores if s.total_score < 40)
        low_quality_relationship_count = sum(1 for s in relationship_scores if s.total_score < 40)

        # Calculate connectivity bonus
        # Count entities that appear in at least one relationship
        connected_entities: set[int] = set()
        for rel in relationships:
            source_idx = rel.get("source")
            target_idx = rel.get("target")
            if isinstance(source_idx, int) and 0 <= source_idx < entity_count:
                connected_entities.add(source_idx)
            if isinstance(target_idx, int) and 0 <= target_idx < entity_count:
                connected_entities.add(target_idx)

        connectivity_bonus = len(connected_entities) * 10.0
        connectivity_ratio = len(connected_entities) / entity_count if entity_count > 0 else 0.0

        # Calculate totals
        total_score = entity_contribution + relationship_contribution + connectivity_bonus

        # Average quality uses unweighted scores for clearer interpretation
        raw_entity_total = sum(s.total_score for s in entity_scores)
        raw_relationship_total = sum(s.total_score for s in relationship_scores)
        avg_entity_quality = raw_entity_total / entity_count if entity_count > 0 else 0.0
        avg_relationship_quality = (
            raw_relationship_total / len(relationships) if relationships else 0.0
        )

        # Calculate coverage score (entities per chunk, 0-100)
        coverage_score = 0.0
        if chunk_count > 0:
            coverage_score = min(100.0, (entity_count / chunk_count) * 100)

        # Calculate quality grade (0-100, independent of volume)
        (
            quality_grade,
            quality_label,
            density_ratio,
            density_score,
            topology_score,
            pollution_penalty,
            structural_penalty,
            hub_skew,
            reciprocal_rate,
        ) = calculate_quality_grade(
            avg_entity_quality=avg_entity_quality,
            avg_relationship_quality=avg_relationship_quality,
            entity_count=entity_count,
            relationships=relationships,
            connectivity_ratio=connectivity_ratio,
            low_quality_entity_count=low_quality_entity_count,
            low_quality_relationship_count=low_quality_relationship_count,
            target_density=self.target_density,
        )

        return SourceQualityScore(
            source_id=source_id,
            entity_count=entity_count,
            relationship_count=len(relationships),
            entity_contribution=entity_contribution,
            relationship_contribution=relationship_contribution,
            connectivity_bonus=connectivity_bonus,
            total_score=total_score,
            avg_entity_quality=avg_entity_quality,
            avg_relationship_quality=avg_relationship_quality,
            connectivity_ratio=connectivity_ratio,
            quality_grade=quality_grade,
            quality_label=quality_label,
            low_quality_entity_count=low_quality_entity_count,
            low_quality_relationship_count=low_quality_relationship_count,
            density_ratio=density_ratio,
            density_score=density_score,
            topology_score=topology_score,
            pollution_penalty=pollution_penalty,
            structural_penalty=structural_penalty,
            hub_skew=hub_skew,
            reciprocal_rate=reciprocal_rate,
            coverage_score=coverage_score,
            entity_scores=entity_scores,
            relationship_scores=relationship_scores,
        )

    def get_cacheable_scores(
        self,
        source_id: str,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        entity_chunk_mentions: dict[int, int] | None = None,
        chunk_count: int = 0,
    ) -> dict[str, Any]:
        """Calculate scores and return as a flat dict suitable for database caching.

        This method computes all quality scores and returns them in a format
        that can be directly stored in the Source model's cached_* fields.

        Args:
            source_id: Unique identifier for the source.
            entities: List of extracted entities.
            relationships: List of extracted relationships.
            entity_chunk_mentions: Optional mapping of entity index to chunk count.
            chunk_count: Total number of chunks in the source document (for coverage score).

        Returns:
            Dict with all cached_* field values ready for database update.
        """
        score = self.score_source(
            source_id, entities, relationships, entity_chunk_mentions, chunk_count
        )

        return {
            "cached_quality_grade": round(score.quality_grade, 2),
            "cached_quality_label": score.quality_label,
            "cached_richness_score": round(score.total_score, 2),
            "cached_avg_entity_quality": round(score.avg_entity_quality, 2),
            "cached_avg_relationship_quality": round(score.avg_relationship_quality, 2),
            "cached_connectivity_ratio": round(score.connectivity_ratio, 4),
            "cached_topology_score": round(score.topology_score, 2),
            "cached_density_ratio": round(score.density_ratio, 4),
            "cached_density_score": round(score.density_score, 2),
            "cached_pollution_penalty": round(score.pollution_penalty, 2),
            "cached_structural_penalty": round(score.structural_penalty, 2),
            "cached_hub_skew": round(score.hub_skew, 4),
            "cached_reciprocal_rate": round(score.reciprocal_rate, 4),
            "cached_low_quality_entity_count": score.low_quality_entity_count,
            "cached_low_quality_relationship_count": score.low_quality_relationship_count,
            "cached_coverage_score": round(score.coverage_score, 2),
            "cached_scores_at": datetime.now(tz=UTC),
            "cached_scores_version": SCORING_VERSION,
        }


def calculate_entity_score(
    entity: dict[str, Any],
    quality_config: dict[str, Any] | None = None,
    chunk_mentions: int = 1,
) -> EntityQualityScore:
    """Calculate quality score for a single entity.

    Convenience function that creates a QualityScorer and scores an entity.

    Args:
        entity: Entity dict with name, type, description, confidence, properties, aliases.
        quality_config: Optional domain quality_scoring configuration.
        chunk_mentions: Number of chunks this entity appears in.

    Returns:
        EntityQualityScore with breakdown.
    """
    scorer = QualityScorer(quality_config)
    return scorer.score_entity(entity, chunk_mentions)


def calculate_relationship_score(
    relationship: dict[str, Any],
    entity_count: int,
    quality_config: dict[str, Any] | None = None,
    entities: list[dict[str, Any]] | None = None,
) -> RelationshipQualityScore:
    """Calculate quality score for a single relationship.

    Convenience function that creates a QualityScorer and scores a relationship.

    Args:
        relationship: Relationship dict with type, source, target, justification, confidence.
        entity_count: Total number of entities (for validating indices).
        quality_config: Optional domain quality_scoring configuration.
        entities: Optional list of entities (for getting names).

    Returns:
        RelationshipQualityScore with breakdown.
    """
    scorer = QualityScorer(quality_config)
    return scorer.score_relationship(relationship, entity_count, entities)


def calculate_source_score(
    source_id: str,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    quality_config: dict[str, Any] | None = None,
    entity_chunk_mentions: dict[int, int] | None = None,
    chunk_count: int = 0,
) -> SourceQualityScore:
    """Calculate quality score for an entire source.

    Convenience function that creates a QualityScorer and scores a source.

    Args:
        source_id: Unique identifier for the source.
        entities: List of extracted entities.
        relationships: List of extracted relationships.
        quality_config: Optional domain quality_scoring configuration.
        entity_chunk_mentions: Optional mapping of entity index to chunk count.
        chunk_count: Total number of chunks in the source document (for coverage score).

    Returns:
        SourceQualityScore with full breakdown.
    """
    scorer = QualityScorer(quality_config)
    return scorer.score_source(
        source_id, entities, relationships, entity_chunk_mentions, chunk_count
    )


__all__ = [
    "DEFAULT_ENTITY_SCORE",
    "DEFAULT_RELATIONSHIP_SCORE",
    "DEFAULT_TARGET_DENSITY",
    "SCORING_VERSION",
    "STRUCTURAL_PENALTY_MAX",
    "EntityQualityScore",
    "QualityScorer",
    "RelationshipQualityScore",
    "SourceQualityScore",
    "calculate_density_score",
    "calculate_entity_score",
    "calculate_hub_skew",
    "calculate_pollution_penalty",
    "calculate_quality_grade",
    "calculate_reciprocal_rate",
    "calculate_relationship_score",
    "calculate_source_score",
    "calculate_structural_penalty",
]
