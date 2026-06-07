# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for sources mappers module.

Mappers are pure functions that transform source dicts for API responses.
Covers duration calculation, domain-icon enrichment, duration-field injection,
and the quality-config/quality-score helpers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources.mappers import (
    add_duration_fields,
    attach_quality_scores,
    build_domain_icon_map,
    calculate_duration,
    enrich_domain_icons,
    get_quality_config_for_domain,
)


# ---------------------------------------------------------------------------
# calculate_duration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCalculateDuration:
    """Tests for calculate_duration helper."""

    def test_returns_none_when_started_at_missing(self) -> None:
        """Returns None when started_at is None."""
        assert calculate_duration(None, "2026-01-01T00:00:05") is None

    def test_returns_none_when_completed_at_missing(self) -> None:
        """Returns None when completed_at is None."""
        assert calculate_duration("2026-01-01T00:00:00", None) is None

    def test_returns_none_when_both_missing(self) -> None:
        """Returns None when both timestamps are None."""
        assert calculate_duration(None, None) is None

    def test_returns_seconds_for_valid_iso_timestamps(self) -> None:
        """Returns the delta in seconds between two ISO timestamps."""
        result = calculate_duration("2026-01-01T00:00:00", "2026-01-01T00:00:30")
        assert result == 30.0

    def test_handles_trailing_z_suffix(self) -> None:
        """Converts the trailing 'Z' UTC marker to +00:00 correctly."""
        result = calculate_duration("2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z")
        assert result == 10.0

    def test_returns_none_for_invalid_timestamp(self) -> None:
        """Returns None when the timestamp cannot be parsed."""
        result = calculate_duration("not-a-date", "2026-01-01T00:00:10")
        assert result is None

    def test_returns_negative_value_when_end_before_start(self) -> None:
        """Returns a negative number when completed_at precedes started_at."""
        result = calculate_duration("2026-01-01T00:01:00", "2026-01-01T00:00:30")
        assert result == -30.0


# ---------------------------------------------------------------------------
# add_duration_fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddDurationFields:
    """Tests for add_duration_fields helper."""

    def test_adds_all_three_duration_fields(self) -> None:
        """Adds indexing/extraction/commit duration fields to the source dict."""
        source: dict[str, Any] = {
            "indexing_started_at": "2026-01-01T00:00:00",
            "indexing_completed_at": "2026-01-01T00:00:05",
            "extraction_started_at": "2026-01-01T00:01:00",
            "extraction_completed_at": "2026-01-01T00:01:30",
            "commit_started_at": "2026-01-01T00:02:00",
            "commit_completed_at": "2026-01-01T00:02:10",
        }

        result = add_duration_fields(source)

        assert result["indexing_duration_seconds"] == 5.0
        assert result["extraction_duration_seconds"] == 30.0
        assert result["commit_duration_seconds"] == 10.0

    def test_sets_none_when_timestamps_missing(self) -> None:
        """Sets duration fields to None when timestamps are absent."""
        source: dict[str, Any] = {}
        result = add_duration_fields(source)
        assert result["indexing_duration_seconds"] is None
        assert result["extraction_duration_seconds"] is None
        assert result["commit_duration_seconds"] is None

    def test_mutates_source_in_place_and_returns_same_ref(self) -> None:
        """The passed-in dict is mutated and returned (identity preserved)."""
        source: dict[str, Any] = {}
        result = add_duration_fields(source)
        assert result is source
        assert "indexing_duration_seconds" in source


# ---------------------------------------------------------------------------
# enrich_domain_icons
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnrichDomainIcons:
    """Tests for enrich_domain_icons helper."""

    def test_sets_icon_when_domain_present(self) -> None:
        """Attaches extraction_domain_icon based on the lookup map."""
        sources: list[dict[str, Any]] = [
            {"id": "s1", "extraction_domain": "technical"},
            {"id": "s2", "extraction_domain": "medical"},
        ]
        icon_map: dict[str, str | None] = {
            "technical": "CodeIcon",
            "medical": "MedicalIcon",
        }

        enrich_domain_icons(sources, icon_map)

        assert sources[0]["extraction_domain_icon"] == "CodeIcon"
        assert sources[1]["extraction_domain_icon"] == "MedicalIcon"

    def test_skips_sources_without_domain(self) -> None:
        """Leaves extraction_domain_icon untouched when domain is missing."""
        sources: list[dict[str, Any]] = [{"id": "s1", "extraction_domain": None}]
        enrich_domain_icons(sources, {"technical": "CodeIcon"})
        assert "extraction_domain_icon" not in sources[0]

    def test_sets_none_when_domain_not_in_map(self) -> None:
        """Sets icon to None when domain is not present in the lookup map."""
        sources: list[dict[str, Any]] = [{"id": "s1", "extraction_domain": "unknown"}]
        enrich_domain_icons(sources, {"technical": "CodeIcon"})
        assert sources[0]["extraction_domain_icon"] is None

    def test_handles_empty_source_list(self) -> None:
        """Does not raise when given an empty source list."""
        sources: list[dict[str, Any]] = []
        enrich_domain_icons(sources, {"technical": "CodeIcon"})
        assert sources == []


# ---------------------------------------------------------------------------
# build_domain_icon_map
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildDomainIconMap:
    """Tests for build_domain_icon_map helper."""

    def test_returns_icon_map_from_registry(self) -> None:
        """Returns a name → icon mapping built from the domain registry."""
        mock_registry = MagicMock()
        mock_registry.list_domain_info.return_value = [
            {"name": "technical", "icon": "CodeIcon"},
            {"name": "medical", "icon": "MedicalIcon"},
            {"name": "nameless"},
        ]

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=mock_registry,
        ):
            result = build_domain_icon_map("default")

        assert result == {
            "technical": "CodeIcon",
            "medical": "MedicalIcon",
            "nameless": None,
        }

    def test_returns_empty_dict_on_registry_error(self) -> None:
        """Returns an empty dict when the registry raises (never propagates)."""
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            side_effect=RuntimeError("boom"),
        ):
            result = build_domain_icon_map("default")
        assert result == {}


# ---------------------------------------------------------------------------
# get_quality_config_for_domain
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetQualityConfigForDomain:
    """Tests for get_quality_config_for_domain helper."""

    def test_returns_empty_dict_when_domain_none(self) -> None:
        """Returns {} when the domain argument is None."""
        assert get_quality_config_for_domain(None, "default") == {}

    def test_returns_config_from_domain_analyzer(self) -> None:
        """Returns the dict produced by analyzer.get_quality_scoring()."""
        mock_analyzer = MagicMock()
        mock_analyzer.get_quality_scoring.return_value = {"min_score": 50}
        mock_registry = MagicMock()
        mock_registry.get_domain.return_value = mock_analyzer

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=mock_registry,
        ):
            result = get_quality_config_for_domain("technical", "default")

        assert result == {"min_score": 50}

    def test_returns_empty_dict_when_analyzer_missing(self) -> None:
        """Returns {} when the registry does not have the domain."""
        mock_registry = MagicMock()
        mock_registry.get_domain.return_value = None

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=mock_registry,
        ):
            result = get_quality_config_for_domain("unknown", "default")

        assert result == {}

    def test_returns_empty_dict_when_registry_raises(self) -> None:
        """Returns {} when the registry raises an exception."""
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            side_effect=RuntimeError("boom"),
        ):
            result = get_quality_config_for_domain("technical", "default")
        assert result == {}


# ---------------------------------------------------------------------------
# attach_quality_scores
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAttachQualityScores:
    """Tests for attach_quality_scores helper."""

    def test_attaches_rounded_score_to_each_entity(self) -> None:
        """Adds a quality_score (rounded to one decimal) to every entity dict."""
        entities: list[dict[str, Any]] = [
            {"name": "Alice", "source_chunks": ["c1", "c2"]},
            {"name": "Bob", "chunks": ["c1"]},
        ]
        file_info: dict[str, Any] = {"extraction_domain": "technical"}

        mock_score = MagicMock()
        mock_score.total_score = 73.456
        mock_scorer = MagicMock()
        mock_scorer.score_entity.return_value = mock_score

        with (
            patch(
                "chaoscypher_core.services.quality.QualityScorer",
                return_value=mock_scorer,
            ),
            patch(
                "chaoscypher_cortex.features.sources.mappers.get_quality_config_for_domain",
                return_value={"weight": 1},
            ),
        ):
            attach_quality_scores(entities, file_info, "default")

        assert entities[0]["quality_score"] == 73.5
        assert entities[1]["quality_score"] == 73.5
        assert mock_scorer.score_entity.call_count == 2

    def test_uses_default_chunk_count_when_no_chunks(self) -> None:
        """Uses chunk_mentions=1 when entity has no chunks or source_chunks."""
        entities: list[dict[str, Any]] = [{"name": "Alone"}]
        file_info: dict[str, Any] = {"extraction_domain": None}

        mock_score = MagicMock()
        mock_score.total_score = 10.0
        mock_scorer = MagicMock()
        mock_scorer.score_entity.return_value = mock_score

        with (
            patch(
                "chaoscypher_core.services.quality.QualityScorer",
                return_value=mock_scorer,
            ),
            patch(
                "chaoscypher_cortex.features.sources.mappers.get_quality_config_for_domain",
                return_value={},
            ),
        ):
            attach_quality_scores(entities, file_info, "default")

        mock_scorer.score_entity.assert_called_once_with(entities[0], chunk_mentions=1)
        assert entities[0]["quality_score"] == 10.0

    def test_handles_empty_entity_list(self) -> None:
        """Does nothing (and does not raise) when entity list is empty."""
        entities: list[dict[str, Any]] = []
        file_info: dict[str, Any] = {"extraction_domain": "technical"}

        mock_scorer = MagicMock()

        with (
            patch(
                "chaoscypher_core.services.quality.QualityScorer",
                return_value=mock_scorer,
            ),
            patch(
                "chaoscypher_cortex.features.sources.mappers.get_quality_config_for_domain",
                return_value={},
            ),
        ):
            attach_quality_scores(entities, file_info, "default")

        assert entities == []
        mock_scorer.score_entity.assert_not_called()
