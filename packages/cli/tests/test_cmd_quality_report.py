# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``chaoscypher quality report`` command.

Covers:
- Full table report with rich metrics (all grade colours: green/yellow/red)
- Empty-graph branch (no sources → yellow warning)
- Domain filter (--domain)
- Include-domains flag (table + domain comparison)
- JSON format output
- CSV format output
- Output to file (--output)
- Source with no extraction data (skipped)
- Source missing from adapter (skipped)
- Domain filter that excludes all sources
- Multiple domains aggregated correctly
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.quality.report import report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source_summary(
    source_id: str = "if_src1",
    domain: str | None = "technical",
) -> dict[str, Any]:
    """Minimal source summary (from list_files)."""
    return {"id": source_id, "extraction_domain": domain}


def _make_full_source(
    source_id: str = "if_src1",
    title: str = "Test Document",
    domain: str | None = "technical",
    entity_count: int = 5,
    relationship_count: int = 3,
) -> dict[str, Any]:
    """Minimal full source record with extraction_results."""
    entities = [
        {
            "name": f"Entity{i}",
            "type": "Person",
            "description": "A detailed description of the entity that is long enough to score well in the quality assessment criteria and more text.",
            "confidence": 0.9,
            "properties": {"role": "CEO", "age": 45},
            "aliases": ["Alias1"],
            "source_chunks": ["chunk1", "chunk2"],
        }
        for i in range(entity_count)
    ]
    relationships = [
        {
            "type": "KNOWS",
            "source": i % entity_count,
            "target": (i + 1) % entity_count,
            "justification": "They have worked together on multiple projects and share a long history of collaboration.",
            "confidence": 0.85,
        }
        for i in range(relationship_count)
    ]
    return {
        "id": source_id,
        "title": title,
        "extraction_domain": domain,
        "extraction_results": {
            "entities": entities,
            "relationships": relationships,
        },
    }


def _make_mock_score(
    source_id: str = "if_src1",
    quality_grade: float = 75.0,
    quality_label: str = "Excellent",
    entity_count: int = 5,
    relationship_count: int = 3,
) -> MagicMock:
    """Create a mock SourceQualityScore."""
    score = MagicMock()
    score.quality_grade = quality_grade
    score.quality_label = quality_label
    score.entity_count = entity_count
    score.relationship_count = relationship_count
    score.total_score = 125.5
    score.entity_contribution = 80.0
    score.relationship_contribution = 35.0
    score.connectivity_bonus = 10.5
    score.avg_entity_quality = 72.5
    score.avg_relationship_quality = 68.0
    score.connectivity_ratio = 0.8
    score.low_quality_entity_count = 0
    score.low_quality_relationship_count = 0
    return score


def _make_context_and_adapter(
    source_summaries: list[dict],
    full_sources: dict[str, dict | None],
) -> MagicMock:
    """Build a mock CLIContext with list_files / get_file configured."""
    adapter = MagicMock()
    adapter.list_files.return_value = source_summaries
    adapter.get_file.side_effect = lambda sid, dbname: full_sources.get(sid)

    ctx = MagicMock()
    ctx.storage_adapter = adapter
    ctx.database_name = "default"
    return ctx


# ---------------------------------------------------------------------------
# No sources (empty graph)
# ---------------------------------------------------------------------------


class TestEmptyGraph:
    """Commands exit cleanly and warn when no sources exist."""

    def test_no_sources_exits_0_with_warning(self) -> None:
        runner = CliRunner()
        ctx = _make_context_and_adapter([], {})

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            result = runner.invoke(report)

        assert result.exit_code == 0, result.output
        assert "No sources found" in result.output

    def test_all_sources_have_no_extraction_data(self) -> None:
        """Sources that have no entities or relationships are skipped → no results."""
        runner = CliRunner()
        summaries = [_make_source_summary("if_empty")]
        full = {
            "if_empty": {
                "id": "if_empty",
                "title": "Empty",
                "extraction_domain": "technical",
                "extraction_results": {"entities": [], "relationships": []},
            }
        }
        ctx = _make_context_and_adapter(summaries, full)

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch("chaoscypher_core.services.quality.QualityScorer"):
                result = runner.invoke(report)

        assert result.exit_code == 0
        assert "No sources found" in result.output

    def test_source_not_found_in_adapter_is_skipped(self) -> None:
        """If get_file returns None the source is silently skipped."""
        runner = CliRunner()
        summaries = [_make_source_summary("if_missing")]
        ctx = _make_context_and_adapter(summaries, {"if_missing": None})

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch("chaoscypher_core.services.quality.QualityScorer"):
                result = runner.invoke(report)

        assert result.exit_code == 0
        assert "No sources found" in result.output


# ---------------------------------------------------------------------------
# Full table report
# ---------------------------------------------------------------------------


class TestTableReport:
    """Default (table) output covers all grade colour tiers."""

    def _invoke_report(
        self,
        quality_grade: float = 75.0,
        quality_label: str = "Excellent",
        extra_args: list[str] | None = None,
    ) -> Any:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score(quality_grade=quality_grade, quality_label=quality_label)
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        args = list(extra_args or [])
        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    return runner.invoke(report, args)

    def test_table_report_exits_0(self) -> None:
        result = self._invoke_report()
        assert result.exit_code == 0, result.output

    def test_table_report_shows_summary_header(self) -> None:
        result = self._invoke_report()
        assert "Quality Report Summary" in result.output

    def test_table_report_shows_entity_count(self) -> None:
        result = self._invoke_report()
        assert "Total entities" in result.output

    def test_table_report_shows_relationship_count(self) -> None:
        result = self._invoke_report()
        assert "Total relationships" in result.output

    def test_grade_green_tier(self) -> None:
        """Grade >= 70 → green colour markup."""
        result = self._invoke_report(quality_grade=85.0, quality_label="Outstanding")
        assert result.exit_code == 0
        # The grade 85 should appear in the output
        assert "85" in result.output

    def test_grade_yellow_tier(self) -> None:
        """Grade >= 50 and < 70 → yellow colour markup."""
        result = self._invoke_report(quality_grade=60.0, quality_label="Good")
        assert result.exit_code == 0
        assert "60" in result.output

    def test_grade_red_tier(self) -> None:
        """Grade < 50 → red colour markup."""
        result = self._invoke_report(quality_grade=25.0, quality_label="Low")
        assert result.exit_code == 0
        assert "25" in result.output

    def test_table_shows_quality_label(self) -> None:
        result = self._invoke_report(quality_grade=75.0, quality_label="Excellent")
        assert "Excellent" in result.output


# ---------------------------------------------------------------------------
# Domain filter
# ---------------------------------------------------------------------------


class TestDomainFilter:
    """--domain filters which sources are scored."""

    def test_domain_filter_excludes_non_matching_sources(self) -> None:
        runner = CliRunner()
        summaries = [
            _make_source_summary("if_tech", domain="technical"),
            _make_source_summary("if_legal", domain="legal"),
        ]
        # Only if_tech should be fetched (legal is filtered out)
        full_sources = {
            "if_tech": _make_full_source("if_tech", domain="technical"),
            "if_legal": _make_full_source("if_legal", domain="legal"),
        }
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--domain", "technical"])

        assert result.exit_code == 0
        # Only one source scored → scorer called once
        assert mock_scorer.score_source.call_count == 1

    def test_domain_filter_no_match_shows_no_sources(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary("if_tech", domain="technical")]
        full_sources = {"if_tech": _make_full_source("if_tech", domain="technical")}
        ctx = _make_context_and_adapter(summaries, full_sources)

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch("chaoscypher_core.services.quality.QualityScorer"):
                result = runner.invoke(report, ["--domain", "legal"])

        assert result.exit_code == 0
        assert "No sources found" in result.output


# ---------------------------------------------------------------------------
# Include domains
# ---------------------------------------------------------------------------


class TestIncludeDomains:
    """--include-domains adds domain comparison section to the report."""

    def test_include_domains_shows_domain_comparison_table(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--include-domains"])

        assert result.exit_code == 0
        assert "Domain Comparison" in result.output

    def test_include_domains_without_flag_skips_domain_table(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report)

        assert result.exit_code == 0
        assert "Domain Comparison" not in result.output

    def test_include_domains_low_grade_colour(self) -> None:
        """Domain avg_grade < 50 renders red in the domain comparison table."""
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score(quality_grade=25.0, quality_label="Low")
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--include-domains"])

        assert result.exit_code == 0
        assert "Domain Comparison" in result.output

    def test_include_domains_yellow_grade_colour(self) -> None:
        """Domain avg_grade in [50, 70) renders yellow."""
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score(quality_grade=60.0, quality_label="Good")
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--include-domains"])

        assert result.exit_code == 0
        assert "Domain Comparison" in result.output


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------


class TestJsonFormat:
    """--format json outputs valid JSON with expected structure."""

    def test_json_format_exits_0(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "json"])

        assert result.exit_code == 0, result.output

    def test_json_format_is_valid_json(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "json"])

        parsed = json.loads(result.output)
        assert "summary" in parsed
        assert "sources" in parsed

    def test_json_format_summary_has_required_keys(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "json"])

        parsed = json.loads(result.output)
        summary = parsed["summary"]
        assert "total_sources" in summary
        assert "total_entities" in summary
        assert "total_relationships" in summary
        assert "avg_grade" in summary

    def test_json_with_include_domains_has_domains_key(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "json", "--include-domains"])

        parsed = json.loads(result.output)
        assert "domains" in parsed


# ---------------------------------------------------------------------------
# CSV format
# ---------------------------------------------------------------------------


class TestCsvFormat:
    """--format csv outputs CSV-formatted data."""

    def test_csv_format_exits_0(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "csv"])

        assert result.exit_code == 0, result.output

    def test_csv_format_has_header_row(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "csv"])

        # CSV should have a header row with quality_grade field
        assert "quality_grade" in result.output
        assert "source_id" in result.output


# ---------------------------------------------------------------------------
# Output to file
# ---------------------------------------------------------------------------


class TestOutputToFile:
    """--output writes to file and prints confirmation."""

    def test_json_output_to_file(self, tmp_path: Any) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        output_file = str(tmp_path / "report.json")

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "json", "--output", output_file])

        assert result.exit_code == 0, result.output
        assert "Report written to" in result.output

    def test_csv_output_to_file(self, tmp_path: Any) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        output_file = str(tmp_path / "report.csv")

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "csv", "--output", output_file])

        assert result.exit_code == 0, result.output
        assert "Report written to" in result.output


# ---------------------------------------------------------------------------
# Multiple sources and domains
# ---------------------------------------------------------------------------


class TestMultipleSources:
    """Multiple sources with different domains are aggregated correctly."""

    def test_multiple_sources_all_scored(self) -> None:
        runner = CliRunner()
        summaries = [
            _make_source_summary("if_s1", domain="technical"),
            _make_source_summary("if_s2", domain="legal"),
        ]
        full_sources = {
            "if_s1": _make_full_source("if_s1", domain="technical"),
            "if_s2": _make_full_source("if_s2", domain="legal"),
        }
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report)

        assert result.exit_code == 0
        assert mock_scorer.score_source.call_count == 2

    def test_source_with_no_domain_uses_unknown(self) -> None:
        """Sources with no domain are assigned 'unknown' in the report."""
        runner = CliRunner()
        summaries = [_make_source_summary("if_nodomain", domain=None)]
        full_sources = {"if_nodomain": _make_full_source("if_nodomain", domain=None)}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score()
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report)

        assert result.exit_code == 0
        assert "unknown" in result.output

    def test_sources_with_relationships_aggregated_in_domain_metrics(self) -> None:
        """relationship_count > 0 increments sources_with_relationships in domain aggregate."""
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source()}
        ctx = _make_context_and_adapter(summaries, full_sources)
        # Score with relationship count > 0
        mock_score = _make_mock_score(relationship_count=5)
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--include-domains"])

        assert result.exit_code == 0

    def test_entities_only_source_no_relationship_quality(self) -> None:
        """Source with entities but 0 relationships is still reported."""
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source(relationship_count=0)}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score(relationship_count=0)
        mock_score.avg_relationship_quality = 0.0
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report)

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# avg_entity_quality branch: sources with entity_count == 0 are excluded from avg
# ---------------------------------------------------------------------------


class TestAvgEntityQualityBranch:
    """avg_entity_quality computation handles entity_count == 0 gracefully."""

    def test_all_sources_have_entities_computes_avg(self) -> None:
        runner = CliRunner()
        summaries = [_make_source_summary()]
        full_sources = {"if_src1": _make_full_source(entity_count=5)}
        ctx = _make_context_and_adapter(summaries, full_sources)
        mock_score = _make_mock_score(entity_count=5)
        mock_score.avg_entity_quality = 65.0
        mock_scorer = MagicMock()
        mock_scorer.score_source.return_value = mock_score

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}
            ):
                with patch(
                    "chaoscypher_core.services.quality.QualityScorer",
                    return_value=mock_scorer,
                ):
                    result = runner.invoke(report, ["--format", "json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["summary"]["avg_entity_quality"] >= 0


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    """The report command has the correct CLI name and options."""

    def test_report_cmd_name(self) -> None:
        assert report.name == "report"

    def test_report_cmd_has_format_option(self) -> None:
        params = {p.name for p in report.params}
        assert "output_format" in params

    def test_report_cmd_has_output_option(self) -> None:
        params = {p.name for p in report.params}
        assert "output" in params

    def test_report_cmd_has_domain_option(self) -> None:
        params = {p.name for p in report.params}
        assert "domain" in params

    def test_report_cmd_has_include_domains_flag(self) -> None:
        params = {p.name for p in report.params}
        assert "include_domains" in params

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(report, ["--help"])
        assert result.exit_code == 0
        assert "format" in result.output.lower()
