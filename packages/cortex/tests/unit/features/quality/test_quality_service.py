# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QualityService.

Drives each QualityService method off a MagicMock SQLite adapter so no real
DB access occurs. Covers the cached-valid fast path, missing-source short
circuit, the zero-score no-data branch, the fresh-calc path (QualityScorer +
domain config patched at source), cache-write failure swallowing, batch
recalculation accounting, outdated-source detection, multi-source analysis
filtering/averaging, domain comparison grouping, and summary aggregation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cortex.features.quality import service as service_module
from chaoscypher_cortex.features.quality.service import QualityService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# SCORING_VERSION as bound inside the service module (matches core).
_CURRENT_VERSION = service_module.SCORING_VERSION


def _make_service(adapter: MagicMock | None = None) -> QualityService:
    """Return a QualityService wired with a MagicMock adapter."""
    return QualityService(adapter=adapter or MagicMock(), database_name="default")


def _cached_source(
    source_id: str = "src-1",
    *,
    version: int | None = None,
    cached_at: str | None = "2026-01-01T00:00:00",
    entities_count: int = 10,
    relationships_count: int = 5,
    extraction_complete: bool = True,
    domain: str | None = "general",
) -> dict[str, Any]:
    """Return a source dict with valid cached score fields populated."""
    return {
        "id": source_id,
        "title": f"Source {source_id}",
        "extraction_domain": domain,
        "extraction_complete": extraction_complete,
        "extraction_entities_count": entities_count,
        "extraction_relationships_count": relationships_count,
        "cached_scores_version": _CURRENT_VERSION if version is None else version,
        "cached_scores_at": cached_at,
        "cached_richness_score": 80.0,
        "cached_avg_entity_quality": 60.0,
        "cached_avg_relationship_quality": 55.0,
        "cached_connectivity_ratio": 0.8,
        "cached_quality_grade": 70.0,
        "cached_quality_label": "Good",
        "cached_low_quality_entity_count": 1,
        "cached_low_quality_relationship_count": 0,
        "cached_density_ratio": 0.5,
        "cached_density_score": 50.0,
        "cached_topology_score": 60.0,
        "cached_pollution_penalty": 2.0,
        "cached_structural_penalty": 1.0,
        "cached_hub_skew": 1.0,
        "cached_reciprocal_rate": 0.0,
        "cached_coverage_score": 80.0,
    }


def _fake_score(
    *,
    total_score: float = 88.0,
    entity_count: int = 3,
    relationship_count: int = 2,
    avg_entity_quality: float = 70.0,
    avg_relationship_quality: float = 65.0,
    connectivity_ratio: float = 0.9,
) -> SimpleNamespace:
    """Return a stand-in SourceQualityScore with all attributes the service reads."""
    return SimpleNamespace(
        entity_count=entity_count,
        relationship_count=relationship_count,
        entity_contribution=50.0,
        relationship_contribution=25.0,
        connectivity_bonus=5.0,
        total_score=total_score,
        avg_entity_quality=avg_entity_quality,
        avg_relationship_quality=avg_relationship_quality,
        connectivity_ratio=connectivity_ratio,
        quality_grade=72.0,
        quality_label="Good",
        low_quality_entity_count=0,
        low_quality_relationship_count=0,
        density_ratio=0.5,
        density_score=50.0,
        topology_score=60.0,
        pollution_penalty=2.0,
        structural_penalty=1.0,
        hub_skew=1.0,
        reciprocal_rate=0.0,
        coverage_score=80.0,
        entity_scores=[
            SimpleNamespace(
                entity_name="Alice",
                entity_type="Person",
                description_score=10.0,
                confidence_score=8.0,
                cross_chunk_score=5.0,
                properties_score=4.0,
                aliases_score=2.0,
                type_value_score=3.0,
                total_score=32.0,
            )
        ],
        relationship_scores=[
            SimpleNamespace(
                relationship_type="knows",
                source_entity="Alice",
                target_entity="Bob",
                justification_score=6.0,
                confidence_score=7.0,
                specificity_score=4.0,
                valid_refs_score=3.0,
                total_score=20.0,
            )
        ],
    )


def _cacheable_scores() -> dict[str, Any]:
    """Return a minimal cacheable-scores dict (only the logged key is read)."""
    return {"cached_quality_grade": 72.0}


def _patch_scorer(score: SimpleNamespace | None = None) -> Any:
    """Patch QualityScorer in the service module to return a stub scorer."""
    scorer_instance = MagicMock()
    scorer_instance.score_source.return_value = score or _fake_score()
    scorer_instance.get_cacheable_scores.return_value = _cacheable_scores()
    return patch.object(
        service_module, "QualityScorer", return_value=scorer_instance
    ), scorer_instance


# ---------------------------------------------------------------------------
# score_source
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreSource:
    """Tests for QualityService.score_source."""

    def test_returns_none_when_source_missing(self) -> None:
        """score_source returns None when get_file returns falsy."""
        adapter = MagicMock()
        adapter.get_file.return_value = None
        service = _make_service(adapter)

        assert service.score_source("missing") is None
        adapter.get_file.assert_called_once_with("missing", "default")

    def test_uses_cached_fast_path(self) -> None:
        """score_source returns cache-built result without loading entity rows."""
        adapter = MagicMock()
        adapter.get_file.return_value = _cached_source("src-1")
        service = _make_service(adapter)

        result = service.score_source("src-1")

        assert result is not None
        assert result["source_id"] == "src-1"
        assert result["total_score"] == 80.0
        assert result["quality_label"] == "Good"
        # Fast path must not touch the per-source entity / relationship tables.
        adapter.list_source_entities.assert_not_called()
        adapter.list_source_relationships.assert_not_called()

    def test_zero_score_when_no_entities_and_no_relationships(self) -> None:
        """score_source returns the all-zero result when extraction data is empty."""
        adapter = MagicMock()
        # Source with no valid cache so we fall through to the data load.
        adapter.get_file.return_value = _cached_source("src-1", version=1, cached_at=None)
        adapter.list_source_entities.return_value = []
        adapter.list_source_relationships.return_value = []
        service = _make_service(adapter)

        result = service.score_source("src-1", include_details=True)

        assert result is not None
        assert result["entity_count"] == 0
        assert result["relationship_count"] == 0
        assert result["total_score"] == 0.0
        assert result["quality_label"] == "Low"
        assert result["entity_scores"] == []
        assert result["relationship_scores"] == []

    def test_fresh_calculation_path(self) -> None:
        """score_source calculates fresh when cache invalid and data present."""
        adapter = MagicMock()
        adapter.get_file.return_value = _cached_source(
            "src-1", version=1, cached_at=None, domain="medical"
        )
        adapter.list_source_entities.return_value = [
            {"name": "Alice", "source_chunks": [1, 2]},
        ]
        adapter.list_source_relationships.return_value = [
            {"source": "Alice", "target": "Bob"},
        ]
        service = _make_service(adapter)

        scorer_patch, scorer_instance = _patch_scorer()
        with (
            scorer_patch,
            patch.object(
                service, "_get_quality_config_for_domain", return_value={"cfg": 1}
            ) as cfg_patch,
        ):
            result = service.score_source("src-1", include_details=True)

        assert result is not None
        assert result["source_id"] == "src-1"
        assert result["domain"] == "medical"
        assert result["total_score"] == 88.0
        assert result["entity_count"] == 3
        # include_details surfaces per-entity / per-relationship breakdowns.
        assert result["entity_scores"][0]["entity_name"] == "Alice"
        assert result["relationship_scores"][0]["relationship_type"] == "knows"
        cfg_patch.assert_called_once_with("medical")
        # Cache write must be attempted.
        adapter.update_file.assert_called_once()

    def test_force_recalculate_bypasses_cache(self) -> None:
        """force_recalculate=True skips the cached fast path even when valid."""
        adapter = MagicMock()
        adapter.get_file.return_value = _cached_source("src-1")  # valid cache
        adapter.list_source_entities.return_value = [{"name": "Alice"}]
        adapter.list_source_relationships.return_value = []
        service = _make_service(adapter)

        scorer_patch, _ = _patch_scorer()
        with scorer_patch, patch.object(service, "_get_quality_config_for_domain", return_value={}):
            result = service.score_source("src-1", force_recalculate=True)

        assert result is not None
        assert result["total_score"] == 88.0
        adapter.list_source_entities.assert_called_once()

    def test_calculate_loads_data_when_not_prepped(self) -> None:
        """_calculate_and_cache_scores loads rows itself when source lacks them."""
        adapter = MagicMock()
        adapter.list_source_entities.return_value = [{"name": "Alice"}]
        adapter.list_source_relationships.return_value = [{"source": "Alice"}]
        service = _make_service(adapter)
        # A bare source dict without the _entities / _relationships keys.
        source = {"id": "src-1", "title": "T", "extraction_domain": None}

        scorer_patch, _ = _patch_scorer()
        with scorer_patch, patch.object(service, "_get_quality_config_for_domain", return_value={}):
            result = service._calculate_and_cache_scores(source)

        adapter.list_source_entities.assert_called_once_with("src-1", "default")
        adapter.list_source_relationships.assert_called_once_with("src-1", "default")
        assert result["total_score"] == 88.0

    def test_calculate_raises_without_id(self) -> None:
        """_calculate_and_cache_scores rejects a source without an id."""
        service = _make_service()
        with pytest.raises(ValueError, match="must have an ID"):
            service._calculate_and_cache_scores({"id": None})

    def test_has_valid_cached_scores_branches(self) -> None:
        """_has_valid_cached_scores distinguishes missing / stale / current cache."""
        service = _make_service()
        # Missing cached_at → False.
        assert (
            service._has_valid_cached_scores(
                {"cached_scores_version": _CURRENT_VERSION, "cached_scores_at": None}
            )
            is False
        )
        # Missing version → False.
        assert (
            service._has_valid_cached_scores(
                {"cached_scores_version": None, "cached_scores_at": "t"}
            )
            is False
        )
        # Stale version → False.
        assert (
            service._has_valid_cached_scores({"cached_scores_version": 1, "cached_scores_at": "t"})
            is False
        )
        # Current version → True.
        assert (
            service._has_valid_cached_scores(
                {"cached_scores_version": _CURRENT_VERSION, "cached_scores_at": "t"}
            )
            is True
        )

    def test_cache_write_exception_is_swallowed(self) -> None:
        """A failing update_file during caching does not break scoring."""
        adapter = MagicMock()
        adapter.get_file.return_value = _cached_source("src-1", version=1, cached_at=None)
        adapter.list_source_entities.return_value = [{"name": "Alice"}]
        adapter.list_source_relationships.return_value = []
        adapter.update_file.side_effect = RuntimeError("db locked")
        service = _make_service(adapter)

        scorer_patch, _ = _patch_scorer()
        with scorer_patch, patch.object(service, "_get_quality_config_for_domain", return_value={}):
            result = service.score_source("src-1")

        # Result is still returned despite the cache write blowing up.
        assert result is not None
        assert result["total_score"] == 88.0


# ---------------------------------------------------------------------------
# _get_quality_config_for_domain
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQualityConfigForDomain:
    """Tests for QualityService._get_quality_config_for_domain."""

    def test_returns_empty_for_no_domain(self) -> None:
        """No domain short-circuits to an empty config."""
        service = _make_service()
        assert service._get_quality_config_for_domain(None) == {}
        assert service._get_quality_config_for_domain("") == {}

    def test_returns_domain_scoring_config(self) -> None:
        """Resolved analyzer's get_quality_scoring result is returned."""
        analyzer = MagicMock()
        analyzer.get_quality_scoring.return_value = {"weights": {"x": 1}}
        registry = MagicMock()
        registry.get_domain.return_value = analyzer
        service = _make_service()

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ):
            result = service._get_quality_config_for_domain("medical")

        assert result == {"weights": {"x": 1}}

    def test_swallows_registry_exception(self) -> None:
        """A registry failure is logged and yields an empty config."""
        service = _make_service()
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            side_effect=RuntimeError("boom"),
        ):
            assert service._get_quality_config_for_domain("medical") == {}


# ---------------------------------------------------------------------------
# recalculate_source_scores / recalculate_all_scores
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecalculate:
    """Tests for the recalculation methods."""

    def test_recalculate_source_scores_forces_recalc(self) -> None:
        """recalculate_source_scores delegates with force_recalculate=True."""
        service = _make_service()
        with patch.object(service, "score_source", return_value={"total_score": 1.0}) as mock_score:
            result = service.recalculate_source_scores("src-1")

        mock_score.assert_called_once_with("src-1", include_details=False, force_recalculate=True)
        assert result == {"total_score": 1.0}

    def test_recalculate_all_skips_and_counts(self) -> None:
        """recalculate_all_scores honours domain filter and entity-count skip."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            # Recalculated: matches domain, has entities.
            {"id": "a", "extraction_domain": "med", "extraction_entities_count": 5},
            # Skipped: wrong domain.
            {"id": "b", "extraction_domain": "law", "extraction_entities_count": 5},
            # Skipped: zero entities.
            {"id": "c", "extraction_domain": "med", "extraction_entities_count": 0},
        ]
        service = _make_service(adapter)

        with patch.object(service, "recalculate_source_scores", return_value={}) as mock_recalc:
            result = service.recalculate_all_scores(domain="med")

        assert result["recalculated_count"] == 1
        assert result["errors"] == []
        mock_recalc.assert_called_once_with("a")

    def test_recalculate_all_records_errors(self) -> None:
        """recalculate_all_scores collects per-source errors without aborting."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            {"id": "a", "extraction_entities_count": 5},
            {"id": "b", "extraction_entities_count": 5},
        ]
        service = _make_service(adapter)

        def _side_effect(source_id: str) -> dict[str, Any]:
            if source_id == "a":
                raise RuntimeError("kaboom")
            return {}

        with patch.object(service, "recalculate_source_scores", side_effect=_side_effect):
            result = service.recalculate_all_scores()

        assert result["recalculated_count"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["source_id"] == "a"

    def test_recalculate_all_skips_none_id(self) -> None:
        """recalculate_all_scores skips a source whose id is None."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            {"id": None, "extraction_entities_count": 5},
        ]
        service = _make_service(adapter)

        with patch.object(service, "recalculate_source_scores") as mock_recalc:
            result = service.recalculate_all_scores()

        assert result["recalculated_count"] == 0
        mock_recalc.assert_not_called()


# ---------------------------------------------------------------------------
# get_outdated_sources
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOutdatedSources:
    """Tests for QualityService.get_outdated_sources."""

    def test_flags_version_mismatch_and_skips_incomplete(self) -> None:
        """Only extraction-complete sources with stale cache are flagged."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            # Outdated: complete but old version.
            _cached_source("old", version=1),
            # Up to date: complete with current version → excluded.
            _cached_source("fresh"),
            # Skipped: extraction not complete.
            _cached_source("incomplete", version=1, extraction_complete=False),
        ]
        service = _make_service(adapter)

        outdated = service.get_outdated_sources()

        ids = {s["id"] for s in outdated}
        assert ids == {"old"}
        assert outdated[0]["cached_scores_version"] == 1
        assert outdated[0]["current_version"] == _CURRENT_VERSION


# ---------------------------------------------------------------------------
# analyze_sources
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyzeSources:
    """Tests for QualityService.analyze_sources."""

    def test_filters_and_averages_cached_sources(self) -> None:
        """analyze_sources filters by id/domain/min_entities and averages scores."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            _cached_source("a", domain="med", entities_count=10),
            _cached_source("b", domain="med", entities_count=10),
            # Excluded by source_ids filter.
            _cached_source("c", domain="med", entities_count=10),
            # Excluded by domain mismatch.
            _cached_source("d", domain="law", entities_count=10),
            # Excluded by min_entities.
            _cached_source("e", domain="med", entities_count=2),
        ]
        service = _make_service(adapter)

        result = service.analyze_sources(
            source_ids=["a", "b", "d", "e"], domain="med", min_entities=5
        )

        assert result["total_sources"] == 2
        # Both use cached richness 80.0 → avg 80.0.
        assert result["avg_score"] == 80.0
        assert result["avg_entity_quality"] == 60.0
        assert result["avg_relationship_quality"] == 55.0

    def test_empty_when_no_matches(self) -> None:
        """analyze_sources returns zeroed averages when nothing matches."""
        adapter = MagicMock()
        adapter.list_files.return_value = []
        service = _make_service(adapter)

        result = service.analyze_sources()

        assert result["total_sources"] == 0
        assert result["avg_score"] == 0.0
        assert result["avg_entity_quality"] == 0.0
        assert result["avg_relationship_quality"] == 0.0

    def test_calculates_when_cache_invalid(self) -> None:
        """analyze_sources falls back to score_source for uncached entries."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            _cached_source("a", version=1, cached_at=None, entities_count=10),
        ]
        service = _make_service(adapter)

        with patch.object(
            service,
            "score_source",
            return_value={
                "total_score": 90.0,
                "entity_count": 4,
                "relationship_count": 2,
                "avg_entity_quality": 70.0,
                "avg_relationship_quality": 60.0,
            },
        ) as mock_score:
            result = service.analyze_sources()

        mock_score.assert_called_once_with("a", include_details=False)
        assert result["total_sources"] == 1
        assert result["avg_score"] == 90.0


# ---------------------------------------------------------------------------
# compare_domains
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompareDomains:
    """Tests for QualityService.compare_domains."""

    def test_groups_and_sorts_by_score(self) -> None:
        """compare_domains groups sources by domain and sorts by avg score desc."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            {"id": "a", "extraction_domain": "med", "extraction_entities_count": 5},
            {"id": "b", "extraction_domain": "law", "extraction_entities_count": 5},
            # Skipped: zero entities.
            {"id": "c", "extraction_domain": "med", "extraction_entities_count": 0},
        ]
        service = _make_service(adapter)

        def _score(source_id: str, include_details: bool = False) -> dict[str, Any]:
            if source_id == "a":
                return {
                    "total_score": 90.0,
                    "avg_entity_quality": 70.0,
                    "avg_relationship_quality": 65.0,
                    "connectivity_ratio": 0.9,
                    "entity_count": 10,
                    "relationship_count": 5,
                }
            return {
                "total_score": 50.0,
                "avg_entity_quality": 40.0,
                "avg_relationship_quality": 0.0,
                "connectivity_ratio": 0.4,
                "entity_count": 3,
                "relationship_count": 0,
            }

        with patch.object(service, "score_source", side_effect=_score):
            result = service.compare_domains()

        domains = result["domains"]
        assert [d["domain"] for d in domains] == ["med", "law"]
        med = domains[0]
        assert med["source_count"] == 1
        assert med["avg_total_score"] == 90.0
        assert med["total_entities"] == 10
        # law has no source with relationships → avg_relationship_quality 0.0
        assert domains[1]["avg_relationship_quality"] == 0.0

    def test_uses_unknown_domain_label(self) -> None:
        """Sources without a domain are grouped under 'unknown'."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            {"id": "a", "extraction_domain": None, "extraction_entities_count": 5},
        ]
        service = _make_service(adapter)

        with patch.object(
            service,
            "score_source",
            return_value={
                "total_score": 10.0,
                "avg_entity_quality": 5.0,
                "avg_relationship_quality": 0.0,
                "connectivity_ratio": 0.0,
                "entity_count": 1,
                "relationship_count": 0,
            },
        ):
            result = service.compare_domains()

        assert result["domains"][0]["domain"] == "unknown"

    def test_skips_when_score_is_none(self) -> None:
        """A source whose score_source returns None is excluded."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            {"id": "a", "extraction_domain": "med", "extraction_entities_count": 5},
        ]
        service = _make_service(adapter)

        with patch.object(service, "score_source", return_value=None):
            result = service.compare_domains()

        assert result["domains"] == []


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSummary:
    """Tests for QualityService.get_summary."""

    def test_empty_summary(self) -> None:
        """get_summary returns a zeroed summary when no scored sources exist."""
        service = _make_service()
        with patch.object(
            service,
            "analyze_sources",
            return_value={
                "sources": [],
                "avg_score": 0.0,
                "avg_entity_quality": 0.0,
                "avg_relationship_quality": 0.0,
            },
        ):
            summary = service.get_summary()

        assert summary["total_sources"] == 0
        assert summary["top_sources"] == []
        assert summary["bottom_sources"] == []

    def test_populated_summary_top_and_bottom(self) -> None:
        """get_summary aggregates totals and selects top/bottom sources."""
        sources = [
            {
                "source_id": f"s{i}",
                "total_score": float(i * 10),
                "entity_count": i,
                "relationship_count": i,
                "connectivity_ratio": 0.5,
                "quality_grade": 60.0,
                "avg_entity_quality": 50.0,
                "avg_relationship_quality": 40.0,
            }
            for i in range(1, 5)  # scores 10, 20, 30, 40
        ]
        service = _make_service()

        settings = MagicMock()
        settings.quality.top_sources_count = 2

        with (
            patch.object(
                service,
                "analyze_sources",
                return_value={
                    "sources": sources,
                    "avg_score": 25.0,
                    "avg_entity_quality": 50.0,
                    "avg_relationship_quality": 40.0,
                },
            ),
            patch.object(service_module, "get_settings", return_value=settings),
        ):
            summary = service.get_summary()

        assert summary["total_sources"] == 4
        assert summary["total_entities"] == 1 + 2 + 3 + 4
        assert summary["avg_total_score"] == 25.0
        # Top 2 by total_score descending: 40, 30.
        assert [s["total_score"] for s in summary["top_sources"]] == [40.0, 30.0]
        # Bottom 2 (tail of sorted-desc list): 20, 10.
        assert [s["total_score"] for s in summary["bottom_sources"]] == [20.0, 10.0]

    def test_no_bottom_when_count_exceeds_sources(self) -> None:
        """bottom_sources is empty when there are fewer sources than the cutoff."""
        sources = [
            {
                "source_id": "s1",
                "total_score": 10.0,
                "entity_count": 1,
                "relationship_count": 1,
                "connectivity_ratio": 0.5,
                "quality_grade": 60.0,
                "avg_entity_quality": 50.0,
                "avg_relationship_quality": 40.0,
            }
        ]
        service = _make_service()

        settings = MagicMock()
        settings.quality.top_sources_count = 5

        with (
            patch.object(
                service,
                "analyze_sources",
                return_value={
                    "sources": sources,
                    "avg_score": 10.0,
                    "avg_entity_quality": 50.0,
                    "avg_relationship_quality": 40.0,
                },
            ),
            patch.object(service_module, "get_settings", return_value=settings),
        ):
            summary = service.get_summary()

        assert summary["top_sources"] == sources
        assert summary["bottom_sources"] == []
