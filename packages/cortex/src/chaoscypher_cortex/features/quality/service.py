# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality Scoring Service.

Provides extraction quality evaluation across sources with caching support.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.quality import SCORING_VERSION, QualityScorer


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

logger = structlog.get_logger(__name__)


class QualityService:
    """Service for evaluating extraction quality.

    Uses the core QualityScorer to evaluate entities, relationships,
    and overall source quality based on domain-specific scoring rules.

    Supports cached scores for performance - scores are calculated after
    extraction completes and cached in the Source model.

    Attributes:
        adapter: SQLite adapter for accessing source data.
        database_name: Name of the current database.
    """

    def __init__(self, adapter: SqliteAdapter, database_name: str) -> None:
        """Initialize quality service.

        Args:
            adapter: SQLite adapter for data access.
            database_name: Current database name.
        """
        self.adapter = adapter
        self.database_name = database_name

    def _get_quality_config_for_domain(self, domain: str | None) -> dict[str, Any]:
        """Load quality_scoring config for a domain.

        Args:
            domain: Domain name to load config for.

        Returns:
            Quality scoring configuration dict, or empty dict if not found.
        """
        if not domain:
            return {}

        try:
            from chaoscypher_core.services.sources.engine.extraction.domains import (
                get_domain_registry,
            )

            registry = get_domain_registry(database_name=self.database_name)
            analyzer = registry.get_domain(domain)
            if analyzer and hasattr(analyzer, "get_quality_scoring"):
                return analyzer.get_quality_scoring()  # type: ignore[no-any-return]
        except Exception as e:
            logger.warning(
                "failed_to_load_domain_quality_config",
                domain=domain,
                error=str(e),
            )
        return {}

    def _has_valid_cached_scores(self, source: dict[str, Any]) -> bool:
        """Check if source has valid cached scores.

        Args:
            source: Source dict from adapter.

        Returns:
            True if cached scores exist and version matches current SCORING_VERSION.
        """
        cached_version = source.get("cached_scores_version")
        cached_at = source.get("cached_scores_at")

        if cached_at is None or cached_version is None:
            return False

        is_valid: bool = cached_version == SCORING_VERSION
        return is_valid

    def _build_result_from_cache(
        self,
        source: dict[str, Any],
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Build result dict from cached score fields.

        Args:
            source: Source dict with cached_* fields populated.
            include_details: Include individual entity/relationship breakdowns.

        Returns:
            Quality score breakdown dict matching the API response format.
        """
        # Use the pre-computed counts instead of loading extraction_results
        # This avoids loading the potentially large extraction_results JSON
        entity_count = source.get("extraction_entities_count") or 0
        relationship_count = source.get("extraction_relationships_count") or 0

        result = {
            "source_id": source.get("id"),
            "source_title": source.get("title"),
            "domain": source.get("extraction_domain"),
            "entity_count": entity_count,
            "relationship_count": relationship_count,
            # These are computed values not cached - calculate from richness
            "entity_contribution": 0.0,  # Not cached - would need recalculation
            "relationship_contribution": 0.0,  # Not cached
            "connectivity_bonus": 0.0,  # Not cached
            "total_score": source.get("cached_richness_score", 0.0),
            "avg_entity_quality": source.get("cached_avg_entity_quality", 0.0),
            "avg_relationship_quality": source.get("cached_avg_relationship_quality", 0.0),
            "connectivity_ratio": source.get("cached_connectivity_ratio", 0.0),
            "quality_grade": source.get("cached_quality_grade", 0.0),
            "quality_label": source.get("cached_quality_label", "Low"),
            "low_quality_entity_count": source.get("cached_low_quality_entity_count", 0),
            "low_quality_relationship_count": source.get(
                "cached_low_quality_relationship_count", 0
            ),
            "density_ratio": source.get("cached_density_ratio", 0.0),
            "density_score": source.get("cached_density_score", 0.0),
            "topology_score": source.get("cached_topology_score", 0.0),
            "pollution_penalty": source.get("cached_pollution_penalty", 0.0),
            "structural_penalty": source.get("cached_structural_penalty", 0.0),
            "hub_skew": source.get("cached_hub_skew", 1.0),
            "reciprocal_rate": source.get("cached_reciprocal_rate", 0.0),
            "coverage_score": source.get("cached_coverage_score", 0.0),
        }

        # For cached results, we don't include details by default
        # If details are needed, we need to recalculate
        if include_details:
            # Details require recalculation - can't cache individual breakdowns
            result["entity_scores"] = None
            result["relationship_scores"] = None

        return result

    def _calculate_and_cache_scores(
        self,
        source: dict[str, Any],
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Calculate scores fresh and cache them.

        Args:
            source: Source dict from adapter.
            include_details: Include individual entity/relationship breakdowns.

        Returns:
            Quality score breakdown dict.
        """
        source_id = source.get("id")
        if source_id is None:
            raise ValueError("Source must have an ID")

        # Entities / relationships are attached by ``score_source``
        # after a fresh read from the per-source tables. Fall back to
        # an explicit load when the caller invoked us without prepping
        # the dict (no consumer does this today, but keep the contract
        # safe).
        entities = source.get("_entities")
        relationships = source.get("_relationships")
        if entities is None:
            entities = self.adapter.list_source_entities(source_id, self.database_name)
        if relationships is None:
            relationships = self.adapter.list_source_relationships(source_id, self.database_name)

        # Get domain-specific quality config
        domain = source.get("extraction_domain")
        quality_config = self._get_quality_config_for_domain(domain)

        # Create scorer and score the source
        scorer = QualityScorer(quality_config)

        # Build entity chunk mentions from extraction data if available
        entity_chunk_mentions: dict[int, int] = {}
        for idx, entity in enumerate(entities):
            chunks = entity.get("source_chunks", []) or entity.get("chunks", [])
            entity_chunk_mentions[idx] = len(chunks) if chunks else 1

        chunk_count = source.get("chunk_count", 0) or 0

        score = scorer.score_source(
            source_id=source_id,
            entities=entities,
            relationships=relationships,
            entity_chunk_mentions=entity_chunk_mentions,
            chunk_count=chunk_count,
        )

        # Cache the scores
        try:
            cached_scores = scorer.get_cacheable_scores(
                source_id=source_id,
                entities=entities,
                relationships=relationships,
                entity_chunk_mentions=entity_chunk_mentions,
                chunk_count=chunk_count,
            )
            self.adapter.update_file(
                source_id, database_name=self.database_name, updates=cached_scores
            )
            logger.debug(
                "quality_scores_cached",
                source_id=source_id,
                quality_grade=cached_scores["cached_quality_grade"],
            )
        except Exception as cache_err:
            logger.warning(
                "failed_to_cache_quality_scores",
                source_id=source_id,
                error=str(cache_err),
            )

        result = {
            "source_id": source_id,
            "source_title": source.get("title"),
            "domain": domain,
            "entity_count": score.entity_count,
            "relationship_count": score.relationship_count,
            "entity_contribution": round(score.entity_contribution, 2),
            "relationship_contribution": round(score.relationship_contribution, 2),
            "connectivity_bonus": round(score.connectivity_bonus, 2),
            "total_score": round(score.total_score, 2),
            "avg_entity_quality": round(score.avg_entity_quality, 2),
            "avg_relationship_quality": round(score.avg_relationship_quality, 2),
            "connectivity_ratio": round(score.connectivity_ratio, 3),
            "quality_grade": round(score.quality_grade, 1),
            "quality_label": score.quality_label,
            "low_quality_entity_count": score.low_quality_entity_count,
            "low_quality_relationship_count": score.low_quality_relationship_count,
            "density_ratio": round(score.density_ratio, 3),
            "density_score": round(score.density_score, 2),
            "topology_score": round(score.topology_score, 2),
            "pollution_penalty": round(score.pollution_penalty, 2),
            "structural_penalty": round(score.structural_penalty, 2),
            "hub_skew": round(score.hub_skew, 3),
            "reciprocal_rate": round(score.reciprocal_rate, 3),
            "coverage_score": round(score.coverage_score, 2),
        }

        if include_details:
            result["entity_scores"] = [
                {
                    "entity_name": s.entity_name,
                    "entity_type": s.entity_type,
                    "description_score": round(s.description_score, 2),
                    "confidence_score": round(s.confidence_score, 2),
                    "cross_chunk_score": round(s.cross_chunk_score, 2),
                    "properties_score": round(s.properties_score, 2),
                    "aliases_score": round(s.aliases_score, 2),
                    "type_value_score": round(s.type_value_score, 2),
                    "total_score": round(s.total_score, 2),
                }
                for s in score.entity_scores
            ]
            result["relationship_scores"] = [
                {
                    "relationship_type": s.relationship_type,
                    "source_entity": s.source_entity,
                    "target_entity": s.target_entity,
                    "justification_score": round(s.justification_score, 2),
                    "confidence_score": round(s.confidence_score, 2),
                    "specificity_score": round(s.specificity_score, 2),
                    "valid_refs_score": round(s.valid_refs_score, 2),
                    "total_score": round(s.total_score, 2),
                }
                for s in score.relationship_scores
            ]

        return result

    def score_source(
        self,
        source_id: str,
        include_details: bool = False,
        force_recalculate: bool = False,
    ) -> dict[str, Any] | None:
        """Score a single source's extraction quality.

        Uses cached scores when available and valid. Falls back to calculation
        when cache is missing, outdated, or force_recalculate is True.

        Args:
            source_id: ID of the source to score.
            include_details: Include individual entity/relationship breakdowns.
            force_recalculate: Bypass cache and recalculate fresh scores.

        Returns:
            Quality score breakdown dict, or None if source not found.
        """
        # Light source load - includes cached_* and extraction counts, but
        # excludes the heavy extraction_results JSON column.
        source = self.adapter.get_file(source_id, self.database_name)
        if not source:
            return None

        # Fast path: use cached scores if available and not forcing recalculation.
        # Must come before any extraction-data load, since get_file()
        # does not load the per-source entity/relationship rows.
        if not force_recalculate and not include_details and self._has_valid_cached_scores(source):
            logger.debug("using_cached_quality_scores", source_id=source_id)
            return self._build_result_from_cache(source, include_details)

        # Need to calculate — load the per-source entity / relationship
        # rows from the dedicated tables (migration 0042).
        entities = self.adapter.list_source_entities(source_id, self.database_name)
        relationships = self.adapter.list_source_relationships(source_id, self.database_name)

        if not entities and not relationships:
            # No extraction data
            return {
                "source_id": source_id,
                "source_title": source.get("title"),
                "domain": source.get("extraction_domain"),
                "entity_count": 0,
                "relationship_count": 0,
                "entity_contribution": 0.0,
                "relationship_contribution": 0.0,
                "connectivity_bonus": 0.0,
                "total_score": 0.0,
                "avg_entity_quality": 0.0,
                "avg_relationship_quality": 0.0,
                "connectivity_ratio": 0.0,
                "quality_grade": 0.0,
                "quality_label": "Low",
                "low_quality_entity_count": 0,
                "low_quality_relationship_count": 0,
                "density_ratio": 0.0,
                "density_score": 0.0,
                "topology_score": 0.0,
                "pollution_penalty": 0.0,
                "structural_penalty": 0.0,
                "hub_skew": 1.0,
                "reciprocal_rate": 0.0,
                "entity_scores": [] if include_details else None,
                "relationship_scores": [] if include_details else None,
            }

        # Attach the freshly-loaded entity / relationship lists to the
        # source dict so the calculator can read them alongside the
        # lightweight metadata.
        source["_entities"] = entities
        source["_relationships"] = relationships

        # Calculate fresh scores
        return self._calculate_and_cache_scores(source, include_details)

    def recalculate_source_scores(self, source_id: str) -> dict[str, Any] | None:
        """Recalculate and cache scores for a single source.

        Args:
            source_id: ID of the source to recalculate.

        Returns:
            Updated quality score dict, or None if source not found.
        """
        return self.score_source(source_id, include_details=False, force_recalculate=True)

    def recalculate_all_scores(
        self,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Recalculate and cache scores for all sources.

        Args:
            domain: Optional domain filter - only recalculate sources in this domain.

        Returns:
            Result dict with recalculated_count and any errors.
        """
        sources = self.adapter.list_files(self.database_name)
        recalculated_count = 0
        errors: list[dict[str, Any]] = []

        for source in sources:
            source_id = source.get("id")

            # Skip if domain filter doesn't match
            if domain and source.get("extraction_domain") != domain:
                continue

            # Skip sources without extracted entities. ``list_files``
            # surfaces the pre-computed counter so we can check this
            # without loading the per-source entity rows.
            if not (source.get("extraction_entities_count") or 0):
                continue

            try:
                if source_id is None:
                    continue
                self.recalculate_source_scores(source_id)
                recalculated_count += 1
            except Exception as e:
                errors.append(
                    {
                        "source_id": source_id,
                        "error": "Score recalculation failed",
                    }
                )
                logger.warning(
                    "score_recalculation_failed",
                    source_id=source_id,
                    error=str(e),
                )

        logger.info(
            "batch_score_recalculation_complete",
            recalculated_count=recalculated_count,
            error_count=len(errors),
            domain_filter=domain,
        )

        return {
            "recalculated_count": recalculated_count,
            "errors": errors,
        }

    def get_outdated_sources(self) -> list[dict[str, Any]]:
        """Get sources with outdated or missing cached scores.

        Returns:
            List of source dicts that need score recalculation.
        """
        sources = self.adapter.list_files(self.database_name)
        outdated = []

        for source in sources:
            # Only consider sources with extraction data
            if not source.get("extraction_complete"):
                continue

            # Check if scores are missing or outdated
            if not self._has_valid_cached_scores(source):
                outdated.append(
                    {
                        "id": source.get("id"),
                        "title": source.get("title"),
                        "cached_scores_version": source.get("cached_scores_version"),
                        "current_version": SCORING_VERSION,
                    }
                )

        return outdated

    def analyze_sources(
        self,
        source_ids: list[str] | None = None,
        domain: str | None = None,
        min_entities: int = 0,
    ) -> dict[str, Any]:
        """Analyze quality across multiple sources.

        Uses cached scores when available to avoid loading extraction_results.

        Args:
            source_ids: Specific source IDs to analyze (None = all).
            domain: Filter by extraction domain.
            min_entities: Minimum entity count to include.

        Returns:
            Analysis results with source scores and aggregated metrics.
        """
        # Get all sources (list_files doesn't load extraction_results for perf)
        sources = self.adapter.list_files(self.database_name)

        # Filter and score sources
        scores: list[dict[str, Any]] = []
        total_entity_quality = 0.0
        total_relationship_quality = 0.0
        total_score = 0.0
        count_with_entities = 0
        count_with_relationships = 0

        for source in sources:
            # Skip if not in requested source_ids
            if source_ids and source.get("id") not in source_ids:
                continue

            # Skip if domain doesn't match
            source_domain = source.get("extraction_domain")
            if domain and source_domain != domain:
                continue

            # Use pre-computed counts (avoids loading extraction_results)
            entity_count = source.get("extraction_entities_count") or 0
            if entity_count < min_entities:
                continue

            # If source has valid cached scores, use them directly
            # This avoids calling get_file() which loads extraction_results
            source_id_val = source.get("id")
            if source_id_val is None:
                continue
            if self._has_valid_cached_scores(source):
                score: dict[str, Any] | None = self._build_result_from_cache(
                    source, include_details=False
                )
            else:
                # Need to calculate - this will load full source
                score = self.score_source(source_id_val, include_details=False)

            if score:
                scores.append(score)
                total_score += score["total_score"]

                if score["entity_count"] > 0:
                    total_entity_quality += score["avg_entity_quality"]
                    count_with_entities += 1

                if score["relationship_count"] > 0:
                    total_relationship_quality += score["avg_relationship_quality"]
                    count_with_relationships += 1

        # Calculate averages
        avg_score = total_score / len(scores) if scores else 0.0
        avg_entity_quality = (
            total_entity_quality / count_with_entities if count_with_entities else 0.0
        )
        avg_relationship_quality = (
            total_relationship_quality / count_with_relationships
            if count_with_relationships
            else 0.0
        )

        return {
            "sources": scores,
            "total_sources": len(scores),
            "avg_score": round(avg_score, 2),
            "avg_entity_quality": round(avg_entity_quality, 2),
            "avg_relationship_quality": round(avg_relationship_quality, 2),
        }

    def compare_domains(self) -> dict[str, Any]:
        """Compare quality performance across domains.

        Returns:
            Domain performance comparison with metrics per domain.
        """
        # Get all sources with extractions
        sources = self.adapter.list_files(self.database_name)

        # Group by domain
        domain_metrics: dict[str, dict[str, Any]] = {}

        for source in sources:
            domain = source.get("extraction_domain") or "unknown"
            entity_count = source.get("extraction_entities_count") or 0

            # Skip sources with no extractions
            if entity_count == 0:
                continue

            # Score the source. Prefer the cached scores already carried on the
            # source dict (from the single list_files() call above) to avoid a
            # guaranteed extra get_file() round trip per source — mirrors the
            # short-circuit analyze_sources() uses.
            source_id_for_score = source.get("id")
            if source_id_for_score is None:
                continue
            if self._has_valid_cached_scores(source):
                score: dict[str, Any] | None = self._build_result_from_cache(
                    source, include_details=False
                )
            else:
                score = self.score_source(source_id_for_score, include_details=False)
            if not score:
                continue

            # Initialize domain metrics if needed
            if domain not in domain_metrics:
                domain_metrics[domain] = {
                    "domain": domain,
                    "source_count": 0,
                    "total_score": 0.0,
                    "total_entity_quality": 0.0,
                    "total_relationship_quality": 0.0,
                    "total_connectivity": 0.0,
                    "total_entities": 0,
                    "total_relationships": 0,
                    "sources_with_relationships": 0,
                }

            metrics = domain_metrics[domain]
            metrics["source_count"] += 1
            metrics["total_score"] += score["total_score"]
            metrics["total_entity_quality"] += score["avg_entity_quality"]
            metrics["total_connectivity"] += score["connectivity_ratio"]
            metrics["total_entities"] += score["entity_count"]
            metrics["total_relationships"] += score["relationship_count"]

            if score["relationship_count"] > 0:
                metrics["total_relationship_quality"] += score["avg_relationship_quality"]
                metrics["sources_with_relationships"] += 1

        # Calculate averages
        domains_list: list[dict[str, Any]] = []
        for domain, metrics in domain_metrics.items():
            count = metrics["source_count"]
            rel_count = metrics["sources_with_relationships"]

            domains_list.append(
                {
                    "domain": domain,
                    "source_count": count,
                    "avg_total_score": round(metrics["total_score"] / count, 2) if count else 0.0,
                    "avg_entity_quality": (
                        round(metrics["total_entity_quality"] / count, 2) if count else 0.0
                    ),
                    "avg_relationship_quality": (
                        round(metrics["total_relationship_quality"] / rel_count, 2)
                        if rel_count
                        else 0.0
                    ),
                    "avg_connectivity_ratio": (
                        round(metrics["total_connectivity"] / count, 3) if count else 0.0
                    ),
                    "total_entities": metrics["total_entities"],
                    "total_relationships": metrics["total_relationships"],
                }
            )

        # Sort by average total score descending
        domains_list.sort(key=lambda x: x["avg_total_score"], reverse=True)

        return {"domains": domains_list}

    def get_summary(self) -> dict[str, Any]:
        """Get overall quality summary for the database.

        Returns:
            Summary with totals, averages, and top/bottom sources.
        """
        # Get all scores
        analysis = self.analyze_sources()
        sources = analysis["sources"]

        if not sources:
            return {
                "total_sources": 0,
                "total_entities": 0,
                "total_relationships": 0,
                "avg_total_score": 0.0,
                "avg_entity_quality": 0.0,
                "avg_relationship_quality": 0.0,
                "avg_quality_grade": 0.0,
                "avg_connectivity_ratio": 0.0,
                "top_sources": [],
                "bottom_sources": [],
            }

        # Calculate totals
        total_entities = sum(s["entity_count"] for s in sources)
        total_relationships = sum(s["relationship_count"] for s in sources)
        total_connectivity = sum(s["connectivity_ratio"] for s in sources)
        total_quality_grade = sum(s.get("quality_grade", 0) for s in sources)

        # Sort for top/bottom
        sorted_sources = sorted(sources, key=lambda x: x["total_score"], reverse=True)

        return {
            "total_sources": len(sources),
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "avg_total_score": analysis["avg_score"],
            "avg_entity_quality": analysis["avg_entity_quality"],
            "avg_relationship_quality": analysis["avg_relationship_quality"],
            "avg_quality_grade": round(total_quality_grade / len(sources), 1),
            "avg_connectivity_ratio": round(total_connectivity / len(sources), 3),
            "top_sources": sorted_sources[: get_settings().quality.top_sources_count],
            "bottom_sources": (
                sorted_sources[-get_settings().quality.top_sources_count :]
                if len(sorted_sources) > get_settings().quality.top_sources_count
                else []
            ),
        }
