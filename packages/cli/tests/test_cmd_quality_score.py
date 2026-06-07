# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``chaoscypher quality score <source_id>`` command.

Covers:
- Source not found → Abort with error message
- Source with no extraction data → Abort with warning
- Full rich output (default) — all grade tiers green/yellow/red
- JSON output (--json flag) — valid JSON with all expected fields
- JSON + --details flag — includes entity_scores and relationship_scores
- Rich + --details flag — shows entity and relationship breakdown tables
- Pollution penalty row (pollution_penalty > 0)
- Structural penalty row (structural_penalty > 0)
- Low quality entity / relationship counts shown in richness table
- Quality labels: Outstanding / Excellent / Good / Fair / Low
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.quality.score import score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(
    source_id: str = "if_src1",
    title: str = "Test Document",
    domain: str | None = "technical",
    entity_count: int = 5,
    relationship_count: int = 3,
) -> dict[str, Any]:
    """Return a full source record with extraction_results."""
    entities = [
        {
            "name": f"Entity{i}",
            "type": "Person",
            "description": "A detailed description of the entity for quality test purposes, long enough.",
            "confidence": 0.9,
            "properties": {"role": "CEO"},
            "aliases": ["Alias1"],
            "source_chunks": ["chunk1", "chunk2"],
        }
        for i in range(entity_count)
    ]
    relationships = [
        {
            "type": "KNOWS",
            "source": i % entity_count if entity_count else 0,
            "target": (i + 1) % entity_count if entity_count else 0,
            "justification": "They have worked together and share a long history of collaboration.",
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
    quality_grade: float = 75.0,
    quality_label: str = "Excellent",
    entity_count: int = 5,
    relationship_count: int = 3,
    pollution_penalty: float = 0.0,
    structural_penalty: float = 0.0,
    hub_skew: float = 1.0,
    reciprocal_rate: float = 0.0,
    low_quality_entity_count: int = 0,
    low_quality_relationship_count: int = 0,
    entity_scores: list | None = None,
    relationship_scores: list | None = None,
) -> MagicMock:
    """Create a fully-populated mock SourceQualityScore."""
    score_mock = MagicMock()
    score_mock.quality_grade = quality_grade
    score_mock.quality_label = quality_label
    score_mock.entity_count = entity_count
    score_mock.relationship_count = relationship_count
    score_mock.total_score = 125.5
    score_mock.entity_contribution = 80.0
    score_mock.relationship_contribution = 35.0
    score_mock.connectivity_bonus = 10.5
    score_mock.avg_entity_quality = 72.5
    score_mock.avg_relationship_quality = 68.0
    score_mock.connectivity_ratio = 0.8
    score_mock.low_quality_entity_count = low_quality_entity_count
    score_mock.low_quality_relationship_count = low_quality_relationship_count
    score_mock.density_ratio = 2.0
    score_mock.density_score = 80.0
    score_mock.topology_score = 75.0
    score_mock.pollution_penalty = pollution_penalty
    score_mock.structural_penalty = structural_penalty
    score_mock.hub_skew = hub_skew
    score_mock.reciprocal_rate = reciprocal_rate
    score_mock.entity_scores = entity_scores or []
    score_mock.relationship_scores = relationship_scores or []
    return score_mock


def _make_entity_score(
    name: str = "Entity0",
    entity_type: str = "Person",
    total_score: float = 75.0,
) -> MagicMock:
    es = MagicMock()
    es.entity_name = name
    es.entity_type = entity_type
    es.total_score = total_score
    return es


def _make_rel_score(
    rel_type: str = "KNOWS",
    source_entity: str = "Entity0",
    target_entity: str = "Entity1",
    total_score: float = 70.0,
) -> MagicMock:
    rs = MagicMock()
    rs.relationship_type = rel_type
    rs.source_entity = source_entity
    rs.target_entity = target_entity
    rs.total_score = total_score
    return rs


def _invoke_score(
    source_id: str = "if_src1",
    mock_score_obj: MagicMock | None = None,
    source_record: dict | None = None,
    extra_args: list[str] | None = None,
) -> Any:
    """Invoke the score command with mocked context/scorer."""
    runner = CliRunner()
    if source_record is None:
        source_record = _make_source(source_id)

    adapter = MagicMock()
    adapter.get_file.return_value = source_record

    ctx = MagicMock()
    ctx.storage_adapter = adapter
    ctx.database_name = "default"

    if mock_score_obj is None:
        mock_score_obj = _make_mock_score()

    mock_scorer = MagicMock()
    mock_scorer.score_source.return_value = mock_score_obj

    args = [source_id, *list(extra_args or [])]
    with patch("chaoscypher_cli.context.get_context", return_value=ctx):
        with patch("chaoscypher_cli.commands.quality.utils.get_quality_config", return_value={}):
            with patch(
                "chaoscypher_core.services.quality.QualityScorer",
                return_value=mock_scorer,
            ):
                return runner.invoke(score, args)


# ---------------------------------------------------------------------------
# Source not found
# ---------------------------------------------------------------------------


class TestSourceNotFound:
    """score exits non-zero with an error when the source does not exist."""

    def test_exits_1_when_source_not_found(self) -> None:
        runner = CliRunner()
        adapter = MagicMock()
        adapter.get_file.return_value = None
        ctx = MagicMock()
        ctx.storage_adapter = adapter
        ctx.database_name = "default"

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            result = runner.invoke(score, ["if_notexist"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "if_notexist" in result.output

    def test_error_message_includes_source_id(self) -> None:
        runner = CliRunner()
        adapter = MagicMock()
        adapter.get_file.return_value = None
        ctx = MagicMock()
        ctx.storage_adapter = adapter
        ctx.database_name = "default"

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            result = runner.invoke(score, ["if_missingxyz"])

        assert "if_missingxyz" in result.output


# ---------------------------------------------------------------------------
# Source with no extraction data
# ---------------------------------------------------------------------------


class TestNoExtractionData:
    """score aborts with warning when source has no entities or relationships."""

    def test_exits_1_when_no_extraction_data(self) -> None:
        runner = CliRunner()
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "id": "if_empty",
            "title": "Empty",
            "extraction_domain": "technical",
            "extraction_results": {"entities": [], "relationships": []},
        }
        ctx = MagicMock()
        ctx.storage_adapter = adapter
        ctx.database_name = "default"

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            result = runner.invoke(score, ["if_empty"])

        assert result.exit_code != 0

    def test_warning_message_shown_when_no_extraction_data(self) -> None:
        runner = CliRunner()
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "id": "if_empty",
            "title": "Empty",
            "extraction_domain": "technical",
            "extraction_results": {},
        }
        ctx = MagicMock()
        ctx.storage_adapter = adapter
        ctx.database_name = "default"

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            result = runner.invoke(score, ["if_empty"])

        assert result.exit_code != 0
        assert "if_empty" in result.output

    def test_null_extraction_results_treated_as_empty(self) -> None:
        runner = CliRunner()
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "id": "if_nullext",
            "title": "Null",
            "extraction_domain": "technical",
            "extraction_results": None,
        }
        ctx = MagicMock()
        ctx.storage_adapter = adapter
        ctx.database_name = "default"

        with patch("chaoscypher_cli.context.get_context", return_value=ctx):
            result = runner.invoke(score, ["if_nullext"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Rich output — default (no --json)
# ---------------------------------------------------------------------------


class TestRichOutput:
    """Default rich output renders all key sections."""

    def test_rich_output_exits_0(self) -> None:
        result = _invoke_score()
        assert result.exit_code == 0, result.output

    def test_rich_output_shows_source_panel(self) -> None:
        result = _invoke_score()
        assert "Source" in result.output

    def test_rich_output_shows_quality_grade_panel(self) -> None:
        result = _invoke_score()
        assert "Quality Grade" in result.output

    def test_rich_output_shows_grade_calculation_table(self) -> None:
        result = _invoke_score()
        assert "Grade Calculation" in result.output

    def test_rich_output_shows_topology_table(self) -> None:
        result = _invoke_score()
        assert "Topology" in result.output

    def test_rich_output_shows_richness_table(self) -> None:
        result = _invoke_score()
        assert "Richness" in result.output

    def test_green_grade_tier(self) -> None:
        """Grade >= 70 renders with green colour."""
        result = _invoke_score(
            mock_score_obj=_make_mock_score(quality_grade=80.0, quality_label="Outstanding")
        )
        assert result.exit_code == 0
        assert "80" in result.output

    def test_yellow_grade_tier(self) -> None:
        """Grade >= 50 and < 70 renders with yellow colour."""
        result = _invoke_score(
            mock_score_obj=_make_mock_score(quality_grade=55.0, quality_label="Good")
        )
        assert result.exit_code == 0
        assert "55" in result.output

    def test_red_grade_tier(self) -> None:
        """Grade < 50 renders with red colour."""
        result = _invoke_score(
            mock_score_obj=_make_mock_score(quality_grade=20.0, quality_label="Low")
        )
        assert result.exit_code == 0
        assert "20" in result.output

    def test_pollution_penalty_row_shown_when_nonzero(self) -> None:
        """Pollution penalty row only appears when pollution_penalty > 0."""
        result = _invoke_score(mock_score_obj=_make_mock_score(pollution_penalty=5.0))
        assert result.exit_code == 0
        assert "Pollution Penalty" in result.output

    def test_pollution_penalty_row_hidden_when_zero(self) -> None:
        """Pollution penalty row does not appear when pollution_penalty == 0.
        Note: 'Pollution' does appear in the table title (- Pollution - Structural),
        so we check that the specific penalty ROW label is absent instead.
        """
        result = _invoke_score(mock_score_obj=_make_mock_score(pollution_penalty=0.0))
        assert result.exit_code == 0
        assert "Pollution Penalty" not in result.output

    def test_structural_penalty_row_shown_when_nonzero(self) -> None:
        """Structural penalty row appears with hub_skew and reciprocal_rate details."""
        result = _invoke_score(
            mock_score_obj=_make_mock_score(
                structural_penalty=3.0,
                hub_skew=4.5,
                reciprocal_rate=0.15,
            )
        )
        assert result.exit_code == 0
        assert "Structural Penalty" in result.output

    def test_structural_penalty_row_hidden_when_zero(self) -> None:
        """Structural penalty row does not appear when structural_penalty == 0.
        Note: 'Structural' appears in the table title, so we check for the
        specific row label 'Structural Penalty'.
        """
        result = _invoke_score(mock_score_obj=_make_mock_score(structural_penalty=0.0))
        assert result.exit_code == 0
        assert "Structural Penalty" not in result.output

    def test_low_quality_entities_shown_when_nonzero(self) -> None:
        """Low Quality Entities row visible when low_quality_entity_count > 0."""
        result = _invoke_score(mock_score_obj=_make_mock_score(low_quality_entity_count=2))
        assert result.exit_code == 0
        assert "Low Quality Entities" in result.output

    def test_low_quality_relationships_shown_when_nonzero(self) -> None:
        """Low Quality Relationships row visible when low_quality_relationship_count > 0."""
        result = _invoke_score(mock_score_obj=_make_mock_score(low_quality_relationship_count=1))
        assert result.exit_code == 0
        assert "Low Quality Relationships" in result.output

    def test_low_quality_counts_hidden_when_zero(self) -> None:
        """Neither low-quality row appears when counts are zero."""
        result = _invoke_score(
            mock_score_obj=_make_mock_score(
                low_quality_entity_count=0, low_quality_relationship_count=0
            )
        )
        assert "Low Quality Entities" not in result.output
        assert "Low Quality Relationships" not in result.output


# ---------------------------------------------------------------------------
# Quality label tiers
# ---------------------------------------------------------------------------


class TestQualityLabels:
    """All five quality labels are rendered in the output."""

    @staticmethod
    def _check_label(grade: float, label: str) -> None:
        result = _invoke_score(
            mock_score_obj=_make_mock_score(quality_grade=grade, quality_label=label)
        )
        assert result.exit_code == 0
        assert label in result.output

    def test_outstanding_label(self) -> None:
        self._check_label(90.0, "Outstanding")

    def test_excellent_label(self) -> None:
        self._check_label(75.0, "Excellent")

    def test_good_label(self) -> None:
        self._check_label(55.0, "Good")

    def test_fair_label(self) -> None:
        self._check_label(35.0, "Fair")

    def test_low_label(self) -> None:
        self._check_label(20.0, "Low")


# ---------------------------------------------------------------------------
# Details flag — rich output
# ---------------------------------------------------------------------------


class TestDetailsFlag:
    """--details shows per-entity and per-relationship breakdown tables."""

    def test_details_shows_entity_scores_table(self) -> None:
        entity_scores = [
            _make_entity_score("Alice", "Person", 80.0),
            _make_entity_score("Bob", "Person", 45.0),
            _make_entity_score("Corp", "Organization", 25.0),
        ]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(entity_scores=entity_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0
        assert "Entity Scores" in result.output

    def test_details_shows_relationship_scores_table(self) -> None:
        rel_scores = [
            _make_rel_score("KNOWS", "Alice", "Bob", 80.0),
            _make_rel_score("OWNS", "Corp", "Asset", 45.0),
        ]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(relationship_scores=rel_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0
        assert "Relationship Scores" in result.output

    def test_details_entity_colour_green_tier(self) -> None:
        """Entity score >= 60 renders green."""
        entity_scores = [_make_entity_score("Alice", "Person", 75.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(entity_scores=entity_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0

    def test_details_entity_colour_yellow_tier(self) -> None:
        """Entity score in [40, 60) renders yellow."""
        entity_scores = [_make_entity_score("Bob", "Person", 50.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(entity_scores=entity_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0

    def test_details_entity_colour_red_tier(self) -> None:
        """Entity score < 40 renders red."""
        entity_scores = [_make_entity_score("Charlie", "Person", 30.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(entity_scores=entity_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0

    def test_details_no_entity_scores_skips_table(self) -> None:
        """If entity_scores list is empty, entity table is not shown."""
        result = _invoke_score(
            mock_score_obj=_make_mock_score(entity_scores=[]),
            extra_args=["--details"],
        )
        assert result.exit_code == 0
        assert "Entity Scores" not in result.output

    def test_details_no_relationship_scores_skips_table(self) -> None:
        """If relationship_scores list is empty, relationship table is not shown."""
        result = _invoke_score(
            mock_score_obj=_make_mock_score(relationship_scores=[]),
            extra_args=["--details"],
        )
        assert result.exit_code == 0
        assert "Relationship Scores" not in result.output

    def test_details_rel_colour_green_tier(self) -> None:
        rel_scores = [_make_rel_score("KNOWS", "A", "B", 75.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(relationship_scores=rel_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0

    def test_details_rel_colour_yellow_tier(self) -> None:
        rel_scores = [_make_rel_score("OWNS", "A", "B", 50.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(relationship_scores=rel_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0

    def test_details_rel_colour_red_tier(self) -> None:
        rel_scores = [_make_rel_score("USES", "A", "B", 30.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(relationship_scores=rel_scores),
            extra_args=["--details"],
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    """--json flag outputs valid JSON with all expected fields."""

    def test_json_output_exits_0(self) -> None:
        result = _invoke_score(extra_args=["--json"])
        assert result.exit_code == 0, result.output

    def test_json_output_is_valid_json(self) -> None:
        result = _invoke_score(extra_args=["--json"])
        # Strip any Rich markup from output (console.print_json wraps in markup)
        # Find the JSON content — it starts with '{'
        output = result.output
        # Find start of JSON object
        start = output.find("{")
        assert start != -1, f"No JSON found in output: {output}"
        parsed = json.loads(output[start:].rstrip())
        assert "source_id" in parsed

    def test_json_output_contains_all_required_fields(self) -> None:
        result = _invoke_score(extra_args=["--json"])
        output = result.output
        start = output.find("{")
        parsed = json.loads(output[start:].rstrip())

        required_fields = [
            "source_id",
            "entity_count",
            "relationship_count",
            "quality_grade",
            "quality_label",
            "total_score",
            "avg_entity_quality",
            "avg_relationship_quality",
            "topology_score",
            "pollution_penalty",
            "structural_penalty",
            "hub_skew",
            "reciprocal_rate",
        ]
        for field in required_fields:
            assert field in parsed, f"Missing field: {field}"

    def test_json_output_quality_grade_value(self) -> None:
        result = _invoke_score(
            mock_score_obj=_make_mock_score(quality_grade=82.0),
            extra_args=["--json"],
        )
        output = result.output
        start = output.find("{")
        parsed = json.loads(output[start:].rstrip())
        assert parsed["quality_grade"] == 82.0

    def test_json_with_details_includes_entity_scores(self) -> None:
        entity_scores = [_make_entity_score("Alice", "Person", 80.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(entity_scores=entity_scores),
            extra_args=["--json", "--details"],
        )
        output = result.output
        start = output.find("{")
        parsed = json.loads(output[start:].rstrip())
        assert "entity_scores" in parsed
        assert len(parsed["entity_scores"]) == 1
        assert parsed["entity_scores"][0]["entity_name"] == "Alice"

    def test_json_with_details_includes_relationship_scores(self) -> None:
        rel_scores = [_make_rel_score("KNOWS", "Alice", "Bob", 70.0)]
        result = _invoke_score(
            mock_score_obj=_make_mock_score(relationship_scores=rel_scores),
            extra_args=["--json", "--details"],
        )
        output = result.output
        start = output.find("{")
        parsed = json.loads(output[start:].rstrip())
        assert "relationship_scores" in parsed
        assert len(parsed["relationship_scores"]) == 1
        assert parsed["relationship_scores"][0]["relationship_type"] == "KNOWS"

    def test_json_without_details_no_entity_scores_key(self) -> None:
        result = _invoke_score(extra_args=["--json"])
        output = result.output
        start = output.find("{")
        parsed = json.loads(output[start:].rstrip())
        assert "entity_scores" not in parsed

    def test_json_source_title_included(self) -> None:
        result = _invoke_score(
            source_record=_make_source(title="My Great Doc"),
            extra_args=["--json"],
        )
        output = result.output
        start = output.find("{")
        parsed = json.loads(output[start:].rstrip())
        assert parsed.get("source_title") == "My Great Doc"


# ---------------------------------------------------------------------------
# Domain handling
# ---------------------------------------------------------------------------


class TestDomainHandling:
    """Source domain is forwarded to quality config and shown in output."""

    def test_source_with_no_domain_renders_unknown(self) -> None:
        result = _invoke_score(
            source_record=_make_source(domain=None),
        )
        assert result.exit_code == 0
        assert "unknown" in result.output

    def test_source_with_domain_renders_domain_name(self) -> None:
        result = _invoke_score(
            source_record=_make_source(domain="legal"),
        )
        assert result.exit_code == 0
        assert "legal" in result.output


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    """The score command has the correct CLI name and parameters."""

    def test_score_cmd_name(self) -> None:
        assert score.name == "score"

    def test_score_cmd_has_details_flag(self) -> None:
        params = {p.name for p in score.params}
        assert "details" in params

    def test_score_cmd_has_json_flag(self) -> None:
        params = {p.name for p in score.params}
        assert "output_json" in params

    def test_score_cmd_has_source_id_argument(self) -> None:
        param_names = {p.name for p in score.params}
        assert "source_id" in param_names

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(score, ["--help"])
        assert result.exit_code == 0
        assert "details" in result.output.lower()
