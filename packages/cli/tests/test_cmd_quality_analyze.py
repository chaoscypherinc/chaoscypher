# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for quality analyze, recalculate commands, and quality utils helpers.

Covers:
- analyze happy path (findings rendered)
- analyze empty graph (no sources)
- analyze domain filter
- analyze min-entities filter
- analyze JSON output
- analyze sort options
- analyze score color branches (high/medium/low score, quality)
- analyze low-quality indicator (>5 low-quality entities)
- recalculate happy path
- recalculate no sources
- recalculate domain filter
- recalculate outdated-only filter (up-to-date skipped)
- recalculate source-id filter
- recalculate error handling
- recalculate many errors (>5 truncation)
- utils.build_entity_chunk_mentions
- utils.get_quality_config (no domain, domain found, exception)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.quality.analyze import analyze
from chaoscypher_cli.commands.quality.recalculate import recalculate


# ---------------------------------------------------------------------------
# Patch target constants
# The commands import these lazily (inside function bodies), so we patch at
# the source module namespace where `from X import Y` resolves at call time.
# ---------------------------------------------------------------------------
_QUALITY_SCORER = "chaoscypher_core.services.quality.QualityScorer"
_SCORING_VERSION = "chaoscypher_core.services.quality.SCORING_VERSION"
_GET_QUALITY_CONFIG = "chaoscypher_cli.commands.quality.utils.get_quality_config"
_BUILD_MENTIONS = "chaoscypher_cli.commands.quality.utils.build_entity_chunk_mentions"
_GET_CTX_ANALYZE = "chaoscypher_cli.commands.quality.analyze.get_context"
_GET_CTX_RECALC = "chaoscypher_cli.commands.quality.recalculate.get_context"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_source(
    source_id: str = "if_src0000001",
    title: str = "Test Source",
    domain: str | None = "technical",
    extraction_complete: bool = True,
    cached_scores_version: int | None = None,
) -> dict[str, Any]:
    """Return a minimal source record as returned by list_files."""
    return {
        "id": source_id,
        "title": title,
        "filename": f"{source_id}.pdf",
        "extraction_domain": domain,
        "extraction_complete": extraction_complete,
        "cached_scores_version": cached_scores_version,
    }


def _make_full_source(
    source_id: str = "if_src0000001",
    entities: list[dict] | None = None,
    relationships: list[dict] | None = None,
) -> dict[str, Any]:
    """Return a full source record that includes extraction_results."""
    if entities is None:
        entities = [{"id": f"e{i}", "name": f"Entity{i}"} for i in range(5)]
    if relationships is None:
        relationships = [{"id": f"r{i}"} for i in range(3)]
    return {
        "id": source_id,
        "title": "Test Source",
        "filename": f"{source_id}.pdf",
        "extraction_domain": "technical",
        "extraction_complete": True,
        "extraction_results": {
            "entities": entities,
            "relationships": relationships,
        },
    }


def _make_mock_score(
    entity_count: int = 5,
    relationship_count: int = 3,
    total_score: float = 750.0,
    avg_entity_quality: float = 65.0,
    avg_relationship_quality: float = 50.0,
    connectivity_ratio: float = 0.8,
    low_quality_entity_count: int = 1,
) -> MagicMock:
    """Return a mock SourceQualityScore."""
    score = MagicMock()
    score.entity_count = entity_count
    score.relationship_count = relationship_count
    score.total_score = total_score
    score.avg_entity_quality = avg_entity_quality
    score.avg_relationship_quality = avg_relationship_quality
    score.connectivity_ratio = connectivity_ratio
    score.low_quality_entity_count = low_quality_entity_count
    return score


def _make_cacheable_scores() -> dict[str, Any]:
    return {
        "cached_quality_score": 75.0,
        "cached_scores_version": 7,
    }


def _make_analyze_patches(
    mock_ctx: MagicMock,
    mock_scorer: MagicMock | None = None,
    mentions: dict | None = None,
) -> tuple:
    """Return a tuple of patch context managers for the analyze command."""
    if mock_scorer is None:
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = _make_mock_score()
    return (
        patch(_GET_CTX_ANALYZE, return_value=mock_ctx),
        patch(_QUALITY_SCORER, return_value=mock_scorer),
        patch(_GET_QUALITY_CONFIG, return_value={}),
        patch(_BUILD_MENTIONS, return_value=mentions or {}),
    )


def _make_recalc_patches(
    mock_ctx: MagicMock,
    mock_scorer: MagicMock | None = None,
) -> tuple:
    """Return a tuple of patch context managers for the recalculate command."""
    if mock_scorer is None:
        mock_scorer = MagicMock()
        mock_scorer.get_cacheable_scores.return_value = _make_cacheable_scores()
    return (
        patch(_GET_CTX_RECALC, return_value=mock_ctx),
        patch(_QUALITY_SCORER, return_value=mock_scorer),
        patch(_GET_QUALITY_CONFIG, return_value={}),
        patch(_BUILD_MENTIONS, return_value={}),
    )


# ---------------------------------------------------------------------------
# Tests: analyze command
# ---------------------------------------------------------------------------


class TestAnalyzeHappyPath:
    """analyze with one scored source exits 0 and renders the table."""

    def test_exits_0_and_shows_summary(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, [])

        assert result.exit_code == 0, result.output
        assert "Quality Analysis Summary" in result.output
        assert "Total sources" in result.output

    def test_shows_total_sources_count(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, [])

        assert "1" in result.output

    def test_score_source_called_with_correct_args(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        full = _make_full_source()
        mock_ctx.storage_adapter.get_file.return_value = full

        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score
        mock_mentions = {0: 2}

        p1, p2, p3, _ = _make_analyze_patches(mock_ctx, mock_scorer)
        p4 = patch(_BUILD_MENTIONS, return_value=mock_mentions)
        with p1, p2, p3, p4:
            runner.invoke(analyze, [])

        mock_scorer.score_source.assert_called_once_with(
            source_id="if_src0000001",
            entities=full["extraction_results"]["entities"],
            relationships=full["extraction_results"]["relationships"],
            entity_chunk_mentions=mock_mentions,
        )


class TestAnalyzeEmptyGraph:
    """analyze exits 0 with a 'no sources' message when list is empty."""

    def test_empty_list_files(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = []

        with patch(_GET_CTX_ANALYZE, return_value=mock_ctx):
            result = runner.invoke(analyze, [])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output

    def test_no_entities_no_relationships_skipped(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        full = _make_full_source(entities=[], relationships=[])
        mock_ctx.storage_adapter.get_file.return_value = full

        with patch(_GET_CTX_ANALYZE, return_value=mock_ctx):
            result = runner.invoke(analyze, [])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output

    def test_get_file_returns_none_skipped(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = None

        with patch(_GET_CTX_ANALYZE, return_value=mock_ctx):
            result = runner.invoke(analyze, [])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output


class TestAnalyzeDomainFilter:
    """analyze --domain filters out non-matching sources."""

    def test_domain_filter_excludes_others(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(domain="literary")]

        with patch(_GET_CTX_ANALYZE, return_value=mock_ctx):
            result = runner.invoke(analyze, ["--domain", "technical"])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output

    def test_domain_filter_includes_match(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(domain="technical")]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, ["--domain", "technical"])

        assert result.exit_code == 0, result.output
        assert "No sources found" not in result.output


class TestAnalyzeMinEntitiesFilter:
    """analyze --min-entities skips sources below the threshold."""

    def test_min_entities_filters_out_small_sources(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        # Only 2 entities, but min-entities=10
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source(
            entities=[{"id": "e0"}, {"id": "e1"}],
            relationships=[{"id": "r0"}],
        )

        with patch(_GET_CTX_ANALYZE, return_value=mock_ctx):
            result = runner.invoke(analyze, ["--min-entities", "10"])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output


class TestAnalyzeJsonOutput:
    """analyze --json outputs valid JSON with expected structure."""

    def test_json_output_is_valid_json(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, ["--json"])

        assert result.exit_code == 0, result.output
        output = result.output.strip()
        parsed = json.loads(output)
        assert "sources" in parsed
        assert "total_sources" in parsed
        assert "avg_score" in parsed

    def test_json_sources_list(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        mock_score = _make_mock_score(entity_count=5, relationship_count=3, total_score=750.0)
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, ["--json"])

        parsed = json.loads(result.output.strip())
        assert len(parsed["sources"]) == 1
        src = parsed["sources"][0]
        assert src["entity_count"] == 5
        assert src["total_score"] == 750.0


class TestAnalyzeSortOptions:
    """analyze --sort accepts score/entities/quality."""

    @pytest.mark.parametrize("sort_opt", ["score", "entities", "quality"])
    def test_sort_option_exits_0(self, sort_opt: str) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, ["--sort", sort_opt])

        assert result.exit_code == 0, result.output


class TestAnalyzeScoreColorBranches:
    """Exercises score coloring branches: high ≥1000, medium ≥500, low <500."""

    def _invoke_with_score(
        self,
        total_score: float,
        avg_entity_quality: float,
        low_quality_entity_count: int,
    ) -> str:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        mock_score = _make_mock_score(
            total_score=total_score,
            avg_entity_quality=avg_entity_quality,
            low_quality_entity_count=low_quality_entity_count,
        )
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, [])
        assert result.exit_code == 0, result.output
        return result.output

    def test_high_score_branch(self) -> None:
        # total_score >= 1000 → green
        out = self._invoke_with_score(1200.0, 70.0, 0)
        assert "1200" in out

    def test_medium_score_branch(self) -> None:
        # total_score >= 500 → yellow
        out = self._invoke_with_score(600.0, 45.0, 0)
        assert "600" in out

    def test_low_score_branch(self) -> None:
        # total_score < 500 → white
        out = self._invoke_with_score(200.0, 25.0, 0)
        assert "200" in out

    def test_low_quality_indicator_shown(self) -> None:
        # low_quality_entity_count > 5 shows the asterisk indicator footnote
        out = self._invoke_with_score(750.0, 60.0, 10)
        assert "low-quality entities" in out

    def test_no_relationships_count(self) -> None:
        """Sources with entities > 0 but relationship_count == 0 still appear."""
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source()]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source(
            entities=[{"id": "e0"}],
            relationships=[],
        )

        mock_score = _make_mock_score(
            entity_count=1,
            relationship_count=0,
            avg_relationship_quality=0.0,
        )
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, [])

        assert result.exit_code == 0, result.output

    def test_multiple_sources_avg_computed(self) -> None:
        """Two sources: averages are computed without division errors."""
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [
            _make_source(source_id="if_src0000001"),
            _make_source(source_id="if_src0000002"),
        ]
        mock_ctx.storage_adapter.get_file.side_effect = [
            _make_full_source("if_src0000001"),
            _make_full_source("if_src0000002"),
        ]

        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, [])

        assert result.exit_code == 0, result.output
        assert "2" in result.output


class TestAnalyzeLimitOption:
    """analyze --limit caps the table rows."""

    def test_limit_passed_in_json(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        # 3 sources but limit=2
        mock_ctx.storage_adapter.list_files.return_value = [
            _make_source(source_id=f"if_src{i:07d}") for i in range(3)
        ]
        mock_ctx.storage_adapter.get_file.side_effect = [
            _make_full_source(f"if_src{i:07d}") for i in range(3)
        ]

        p1, p2, p3, p4 = _make_analyze_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(analyze, ["--json", "--limit", "2"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output.strip())
        assert parsed["total_sources"] == 3
        assert len(parsed["sources"]) == 2


class TestAnalyzeDatabaseOption:
    """analyze --database passes the db name to get_context."""

    def test_database_option(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "mydb"
        mock_ctx.storage_adapter.list_files.return_value = []

        with patch(_GET_CTX_ANALYZE, return_value=mock_ctx) as mock_get_ctx:
            runner.invoke(analyze, ["--database", "mydb"])

        mock_get_ctx.assert_called_once_with(database_name="mydb")


# ---------------------------------------------------------------------------
# Tests: recalculate command
# ---------------------------------------------------------------------------


class TestRecalculateHappyPath:
    """recalculate processes matching sources and calls update_file."""

    def test_exits_0_and_prints_summary(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(extraction_complete=True)]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(recalculate, [])

        assert result.exit_code == 0, result.output
        assert "Recalculation Complete" in result.output
        assert "Successfully processed: 1" in result.output

    def test_update_file_called_with_scores(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(extraction_complete=True)]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        mock_cacheable = _make_cacheable_scores()
        mock_scorer = MagicMock()
        mock_scorer.get_cacheable_scores.return_value = mock_cacheable

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            runner.invoke(recalculate, [])

        mock_ctx.storage_adapter.update_file.assert_called_once_with(
            "if_src0000001",
            database_name="default",
            updates=mock_cacheable,
        )


class TestRecalculateNoSources:
    """recalculate exits 0 with 'no sources' message when nothing matches."""

    def test_empty_list_files(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = []

        with patch(_GET_CTX_RECALC, return_value=mock_ctx):
            result = runner.invoke(recalculate, [])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output

    def test_not_extracted_skipped(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(extraction_complete=False)]

        with patch(_GET_CTX_RECALC, return_value=mock_ctx):
            result = runner.invoke(recalculate, [])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output


class TestRecalculateDomainFilter:
    """recalculate --domain filters sources."""

    def test_domain_filter_excludes_others(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [
            _make_source(domain="literary", extraction_complete=True)
        ]

        with patch(_GET_CTX_RECALC, return_value=mock_ctx):
            result = runner.invoke(recalculate, ["--domain", "technical"])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output


class TestRecalculateOutdatedOnly:
    """recalculate --outdated-only skips up-to-date sources."""

    def test_up_to_date_source_skipped(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        # Version matches SCORING_VERSION=7 — should be skipped
        mock_ctx.storage_adapter.list_files.return_value = [
            _make_source(extraction_complete=True, cached_scores_version=7)
        ]

        with (
            patch(_GET_CTX_RECALC, return_value=mock_ctx),
            patch(_SCORING_VERSION, 7),
        ):
            result = runner.invoke(recalculate, ["--outdated-only"])

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output

    def test_outdated_source_included(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        # Version is old (1) — should be included
        mock_ctx.storage_adapter.list_files.return_value = [
            _make_source(extraction_complete=True, cached_scores_version=1)
        ]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx)
        with p1, p2, p3, p4, patch(_SCORING_VERSION, 7):
            result = runner.invoke(recalculate, ["--outdated-only"])

        assert result.exit_code == 0, result.output
        assert "Successfully processed: 1" in result.output

    def test_no_cached_version_included(self) -> None:
        """Sources with cached_scores_version=None should be treated as outdated."""
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [
            _make_source(extraction_complete=True, cached_scores_version=None)
        ]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(recalculate, ["--outdated-only"])

        assert result.exit_code == 0, result.output
        assert "Successfully processed: 1" in result.output


class TestRecalculateSourceIdFilter:
    """recalculate -s filters to specific source IDs."""

    def test_source_id_filter(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [
            _make_source(source_id="if_src0000001", extraction_complete=True),
            _make_source(source_id="if_src0000002", extraction_complete=True),
        ]
        # Only if_src0000001 should be processed
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source("if_src0000001")

        mock_cacheable = _make_cacheable_scores()
        mock_scorer = MagicMock()
        mock_scorer.get_cacheable_scores.return_value = mock_cacheable

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(recalculate, ["-s", "if_src0000001"])

        assert result.exit_code == 0, result.output
        assert "Successfully processed: 1" in result.output
        assert mock_ctx.storage_adapter.update_file.call_count == 1


class TestRecalculateErrorHandling:
    """recalculate reports errors and continues."""

    def test_exception_counted_in_errors(self) -> None:
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(extraction_complete=True)]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source()

        mock_scorer = MagicMock()
        mock_scorer.get_cacheable_scores.side_effect = RuntimeError("scoring failed")

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(recalculate, [])

        assert result.exit_code == 0, result.output
        assert "Errors: 1" in result.output
        assert "scoring failed" in result.output

    def test_many_errors_truncated(self) -> None:
        """When >5 errors occur, the output shows '... and N more errors'."""
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        sources = [
            _make_source(source_id=f"if_src{i:07d}", extraction_complete=True) for i in range(8)
        ]
        mock_ctx.storage_adapter.list_files.return_value = sources
        mock_ctx.storage_adapter.get_file.side_effect = [
            _make_full_source(f"if_src{i:07d}") for i in range(8)
        ]

        mock_scorer = MagicMock()
        mock_scorer.get_cacheable_scores.side_effect = RuntimeError("boom")

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx, mock_scorer)
        with p1, p2, p3, p4:
            result = runner.invoke(recalculate, [])

        assert result.exit_code == 0, result.output
        assert "Errors: 8" in result.output
        assert "more errors" in result.output

    def test_get_file_returns_none_skipped(self) -> None:
        """Sources where get_file returns None are silently skipped."""
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(extraction_complete=True)]
        mock_ctx.storage_adapter.get_file.return_value = None

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(recalculate, [])

        assert result.exit_code == 0, result.output
        assert "Successfully processed: 0" in result.output

    def test_no_entities_no_relationships_skipped(self) -> None:
        """Empty extraction results are silently skipped."""
        runner = CliRunner()
        mock_ctx = MagicMock()
        mock_ctx.database_name = "default"
        mock_ctx.storage_adapter.list_files.return_value = [_make_source(extraction_complete=True)]
        mock_ctx.storage_adapter.get_file.return_value = _make_full_source(
            entities=[], relationships=[]
        )

        p1, p2, p3, p4 = _make_recalc_patches(mock_ctx)
        with p1, p2, p3, p4:
            result = runner.invoke(recalculate, [])

        assert result.exit_code == 0, result.output
        assert "Successfully processed: 0" in result.output


# ---------------------------------------------------------------------------
# Tests: utils.py helpers (direct unit tests)
# ---------------------------------------------------------------------------


class TestBuildEntityChunkMentions:
    """Direct tests for build_entity_chunk_mentions."""

    def test_empty_entities(self) -> None:
        from chaoscypher_cli.commands.quality.utils import build_entity_chunk_mentions

        result = build_entity_chunk_mentions([])
        assert result == {}

    def test_entity_with_source_chunks(self) -> None:
        from chaoscypher_cli.commands.quality.utils import build_entity_chunk_mentions

        entities = [
            {"id": "e0", "source_chunks": ["c1", "c2", "c3"]},
        ]
        result = build_entity_chunk_mentions(entities)
        assert result == {0: 3}

    def test_entity_with_chunks_key(self) -> None:
        from chaoscypher_cli.commands.quality.utils import build_entity_chunk_mentions

        entities = [
            {"id": "e0", "chunks": ["c1"]},
        ]
        result = build_entity_chunk_mentions(entities)
        assert result == {0: 1}

    def test_entity_with_no_chunks_defaults_to_1(self) -> None:
        from chaoscypher_cli.commands.quality.utils import build_entity_chunk_mentions

        entities = [{"id": "e0"}]
        result = build_entity_chunk_mentions(entities)
        assert result == {0: 1}

    def test_multiple_entities_indexed_correctly(self) -> None:
        from chaoscypher_cli.commands.quality.utils import build_entity_chunk_mentions

        entities = [
            {"id": "e0", "source_chunks": ["c1", "c2"]},
            {"id": "e1"},
            {"id": "e2", "source_chunks": ["c1", "c2", "c3", "c4"]},
        ]
        result = build_entity_chunk_mentions(entities)
        assert result == {0: 2, 1: 1, 2: 4}

    def test_empty_source_chunks_list(self) -> None:
        """An entity with source_chunks=[] falls back to 1."""
        from chaoscypher_cli.commands.quality.utils import build_entity_chunk_mentions

        entities = [{"id": "e0", "source_chunks": []}]
        result = build_entity_chunk_mentions(entities)
        # [] is falsy so falls back to 1
        assert result == {0: 1}


class TestGetQualityConfig:
    """Direct tests for get_quality_config."""

    def test_no_domain_returns_empty_dict(self) -> None:
        from chaoscypher_cli.commands.quality.utils import get_quality_config

        result = get_quality_config(None, "testdb")
        assert result == {}

    def test_domain_found_returns_config(self) -> None:
        from chaoscypher_cli.commands.quality.utils import get_quality_config

        mock_analyzer = MagicMock()
        mock_analyzer.get_quality_scoring.return_value = {"threshold": 0.5}

        mock_registry = MagicMock()
        mock_registry.get_domain.return_value = mock_analyzer

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=mock_registry,
        ):
            result = get_quality_config("technical", "testdb")

        assert result == {"threshold": 0.5}
        mock_registry.get_domain.assert_called_once_with("technical")

    def test_domain_not_found_returns_empty_dict(self) -> None:
        from chaoscypher_cli.commands.quality.utils import get_quality_config

        mock_registry = MagicMock()
        mock_registry.get_domain.return_value = None  # domain not found

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=mock_registry,
        ):
            result = get_quality_config("unknown_domain", "testdb")

        assert result == {}

    def test_exception_returns_empty_dict(self) -> None:
        from chaoscypher_cli.commands.quality.utils import get_quality_config

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            side_effect=RuntimeError("registry unavailable"),
        ):
            result = get_quality_config("technical", "testdb")

        assert result == {}

    def test_no_get_quality_scoring_attr_returns_empty_dict(self) -> None:
        """Analyzer exists but lacks get_quality_scoring — returns empty dict."""
        from chaoscypher_cli.commands.quality.utils import get_quality_config

        mock_analyzer = MagicMock(spec=[])  # no attributes
        mock_registry = MagicMock()
        mock_registry.get_domain.return_value = mock_analyzer

        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=mock_registry,
        ):
            result = get_quality_config("technical", "testdb")

        assert result == {}
