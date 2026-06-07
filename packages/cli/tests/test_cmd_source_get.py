# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end coverage for `chaoscypher source get` rendering branches.

Drives the `get` command through Click's CliRunner with a patched
`get_context`, feeding rich file-record dicts that exercise each display
section: status/size formatting, domain rows, quality scores, the extraction
pipeline (full task-stat mode and lite file-level mode), active stage
progress, LLM metrics, error/indexing-stats footers, the not-found path
(exit 1), and the top-level exception handler (exit 1).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cli.commands.source.get import (
    _format_duration,
    _format_duration_seconds,
    get,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SOURCE_ID = "if_getsrc123456"


def _base_record(**overrides: Any) -> dict[str, Any]:
    """Minimal valid file record; override fields per-branch."""
    record: dict[str, Any] = {
        "id": SOURCE_ID,
        "filename": "report.pdf",
        "status": "indexed",
        "file_type": "pdf",
        "file_size": 12_345,
        "filepath": "/staging/report.pdf",
        "created_at": "2026-05-10T18:00:00Z",
        "updated_at": "2026-05-10T19:00:00Z",
        "extraction_depth": "full",
        "extract_entities": True,
    }
    record.update(overrides)
    return record


def _make_ctx(file_record: dict[str, Any] | None) -> MagicMock:
    """Build a mock CLI context whose adapter returns ``file_record``."""
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.get_file.return_value = file_record
    # Default: no per-chunk extraction task stats and no filtering logs.
    ctx.storage_adapter.get_extraction_task_stats.return_value = {}
    ctx.storage_adapter.get_extraction_tasks_filtering_logs.return_value = []
    return ctx


def _invoke(ctx: MagicMock, args: list[str] | None = None):
    from click.testing import CliRunner

    runner = CliRunner()
    # get.py binds `get_context` at import time, so patch the name in the
    # command module's namespace (not the source module).
    with patch("chaoscypher_cli.commands.source.get.get_context", return_value=ctx):
        return runner.invoke(get, [SOURCE_ID, *(args or [])])


# ---------------------------------------------------------------------------
# Not found / error paths
# ---------------------------------------------------------------------------


class TestNotFoundAndErrors:
    def test_not_found_exits_1(self) -> None:
        ctx = _make_ctx(None)
        result = _invoke(ctx)
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
        assert SOURCE_ID in result.output

    def test_top_level_exception_exits_1(self) -> None:
        ctx = _make_ctx(None)
        ctx.storage_adapter.get_file.side_effect = RuntimeError("boom-db")
        result = _invoke(ctx)
        assert result.exit_code == 1
        assert "Error:" in result.output
        assert "boom-db" in result.output

    def test_database_flag_passed_to_context(self) -> None:
        ctx = _make_ctx(_base_record())
        from click.testing import CliRunner

        runner = CliRunner()
        with patch("chaoscypher_cli.commands.source.get.get_context", return_value=ctx) as get_ctx:
            result = runner.invoke(get, [SOURCE_ID, "--database", "mydb"])
        assert result.exit_code == 0, result.output
        get_ctx.assert_called_once_with(database_name="mydb")


# ---------------------------------------------------------------------------
# Core detail panel + size formatting
# ---------------------------------------------------------------------------


class TestCoreDetails:
    def test_indexed_basic_fields_render(self) -> None:
        ctx = _make_ctx(_base_record())
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        # Panel + table fields.
        assert "report.pdf" in result.output
        assert SOURCE_ID in result.output
        assert "indexed" in result.output
        assert "Status" in result.output
        assert "File Type" in result.output
        assert "Extract Entities" in result.output
        # extract_entities True -> "Yes"
        assert "Yes" in result.output

    def test_extract_entities_false_renders_no(self) -> None:
        ctx = _make_ctx(_base_record(extract_entities=False))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "No" in result.output

    def test_size_bytes_tier(self) -> None:
        ctx = _make_ctx(_base_record(file_size=512))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "512 bytes" in result.output
        assert "KB" not in result.output and "MB" not in result.output

    def test_size_kb_tier(self) -> None:
        ctx = _make_ctx(_base_record(file_size=12_345))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "KB" in result.output

    def test_size_mb_tier(self) -> None:
        ctx = _make_ctx(_base_record(file_size=5_000_000))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "MB" in result.output

    def test_failed_status_renders_error_footer(self) -> None:
        ctx = _make_ctx(_base_record(status="failed", error="extraction blew up"))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "failed" in result.output
        assert "Error:" in result.output
        assert "extraction blew up" in result.output


# ---------------------------------------------------------------------------
# Domain row branches
# ---------------------------------------------------------------------------


class TestDomainRow:
    def test_forced_domain_renders(self) -> None:
        ctx = _make_ctx(_base_record(forced_domain="legal", domain_version="2.1.0"))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "legal" in result.output
        assert "forced" in result.output

    def test_detected_domain_renders(self) -> None:
        ctx = _make_ctx(_base_record(detected_domain="technical"))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "technical" in result.output
        assert "auto-detected" in result.output

    def test_domain_changed_warning_when_hash_differs(self) -> None:
        ctx = _make_ctx(
            _base_record(
                forced_domain="technical",
                extraction_domain="technical",
                domain_content_hash="oldhash",
            )
        )
        # Patch the lazily-imported registry so the live fingerprint differs.
        fake_fp = MagicMock()
        fake_fp.content_hash = "newhash"
        fake_registry = MagicMock()
        fake_registry.get_domain_fingerprint.return_value = fake_fp
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=fake_registry,
        ):
            result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "changed since extraction" in result.output

    def test_domain_changed_false_when_registry_raises(self) -> None:
        ctx = _make_ctx(
            _base_record(
                forced_domain="technical",
                extraction_domain="technical",
                domain_content_hash="oldhash",
            )
        )
        with patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            side_effect=ImportError("no registry"),
        ):
            result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "changed since extraction" not in result.output


# ---------------------------------------------------------------------------
# Quality scores section
# ---------------------------------------------------------------------------


class TestQualityScores:
    def test_full_quality_block_renders(self) -> None:
        ctx = _make_ctx(
            _base_record(
                status="committed",
                cached_quality_grade=82.0,
                cached_quality_label="Good",
                cached_avg_entity_quality=78.0,
                cached_avg_relationship_quality=71.0,
                cached_topology_score=64.0,
                cached_pollution_penalty=5.0,
                cached_structural_penalty=8.0,
                cached_hub_skew=2.3,
                cached_reciprocal_rate=0.15,
                cached_low_quality_entity_count=4,
                cached_low_quality_relationship_count=2,
            )
        )
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Quality Score" in result.output
        assert "Grade" in result.output
        assert "82/100" in result.output
        assert "Good" in result.output
        assert "Entity Quality" in result.output
        assert "Relationship Quality" in result.output
        assert "Topology Score" in result.output
        assert "Pollution Penalty" in result.output
        assert "Structural Penalty" in result.output
        assert "hub 2.3x" in result.output
        assert "Low Quality" in result.output
        assert "4 entities" in result.output
        assert "2 relationships" in result.output

    def test_quality_grade_zero_still_renders(self) -> None:
        # grade is `is not None` gated, so 0.0 must still render.
        ctx = _make_ctx(
            _base_record(
                cached_quality_grade=0.0,
                cached_quality_label="Failing",
            )
        )
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Quality Score" in result.output
        assert "0/100" in result.output

    def test_quality_penalties_zero_are_skipped(self) -> None:
        ctx = _make_ctx(
            _base_record(
                cached_quality_grade=90.0,
                cached_quality_label="Excellent",
                cached_pollution_penalty=0.0,
                cached_structural_penalty=0.0,
                cached_low_quality_entity_count=0,
                cached_low_quality_relationship_count=0,
            )
        )
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Pollution Penalty" not in result.output
        assert "Structural Penalty" not in result.output
        assert "Low Quality" not in result.output

    def test_low_quality_only_entities(self) -> None:
        ctx = _make_ctx(
            _base_record(
                cached_quality_grade=60.0,
                cached_quality_label="Fair",
                cached_low_quality_entity_count=3,
                cached_low_quality_relationship_count=0,
            )
        )
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "3 entities" in result.output
        assert "relationships" not in result.output.split("Low Quality")[1][:40]

    def test_no_quality_block_when_grade_absent(self) -> None:
        ctx = _make_ctx(_base_record())
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Quality Score" not in result.output


# ---------------------------------------------------------------------------
# Extraction pipeline section
# ---------------------------------------------------------------------------


class TestExtractionPipelineFull:
    def test_full_stats_with_dedup_and_filtering(self) -> None:
        record = _base_record(
            status="committed",
            chunk_count=40,
            extraction_entities_count=120,
            extraction_relationships_count=60,
            commit_templates_created=8,
            llm_estimated_cost_usd=0.1234,
            llm_model="gpt-4o-mini",
        )
        ctx = _make_ctx(record)
        ctx.storage_adapter.get_extraction_task_stats.return_value = {
            "total_tasks": 10,
            "total_entities": 200,
            "total_relationships": 100,
            "avg_entities_per_task": 20.0,
            "avg_relationships_per_task": 10.0,
            "total_invalid_relationships": 5,
            "avg_invalid_per_task": 0.5,
            "total_retries": 3,
            "avg_duration_ms": 4200,
        }
        ctx.storage_adapter.get_extraction_tasks_filtering_logs.return_value = [
            {
                "filtering_log": {
                    "stages": [
                        {"stage": "entity_dedup", "removed_count": 7},
                        {"stage": "relationship_remap", "removed_count": 4},
                    ]
                }
            }
        ]
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Extraction Pipeline" in result.output
        assert "Groups" in result.output
        assert "from 40 chunks" in result.output
        # dedup: raw 200 -> final 120 => deduped %
        assert "deduped" in result.output
        assert "remapped" in result.output
        assert "Templates" in result.output
        assert "Filtered" in result.output
        assert "Invalid" in result.output
        assert "Retries" in result.output
        assert "Avg Time" in result.output
        assert "Est. Cost" in result.output
        assert "$0.1234" in result.output
        assert "Model" in result.output
        # Per-stage filtering breakdown printed.
        assert "Filtering stages" in result.output
        assert "entity dedup" in result.output

    def test_full_stats_zero_filtered_invalid_retries(self) -> None:
        record = _base_record(
            status="committed",
            extraction_entities_count=10,
            extraction_relationships_count=5,
        )
        ctx = _make_ctx(record)
        ctx.storage_adapter.get_extraction_task_stats.return_value = {
            "total_tasks": 2,
            "total_entities": 10,
            "total_relationships": 5,
            "total_invalid_relationships": 0,
            "total_retries": 0,
            "avg_duration_ms": 0,
        }
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        # final == raw -> no dedup/remap suffix, plain counts.
        assert "Filtered" in result.output
        # All-zero branches render the dim "0" rows.
        assert "Invalid" in result.output
        assert "Retries" in result.output

    def test_raw_only_entities_when_no_final(self) -> None:
        record = _base_record(status="committed")
        ctx = _make_ctx(record)
        ctx.storage_adapter.get_extraction_task_stats.return_value = {
            "total_tasks": 3,
            "total_entities": 30,
            "total_relationships": 12,
            "avg_entities_per_task": 10.0,
            "avg_relationships_per_task": 4.0,
            "total_invalid_relationships": 0,
            "total_retries": 0,
        }
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        # raw-only path shows the per-group averages.
        assert "/group" in result.output

    def test_ollama_cost_local(self) -> None:
        record = _base_record(
            status="committed",
            extraction_entities_count=5,
            llm_model="ollama/llama3",
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "$0.00 (local)" in result.output

    def test_small_cost_renders_under_threshold(self) -> None:
        record = _base_record(
            status="committed",
            extraction_entities_count=5,
            llm_estimated_cost_usd=0.001,
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "<$0.01" in result.output


class TestExtractionPipelineLite:
    def test_lite_mode_file_level_only(self) -> None:
        record = _base_record(
            status="committed",
            chunk_count=12,
            extraction_entities_count=44,
            extraction_relationships_count=22,
            commit_templates_created=3,
        )
        ctx = _make_ctx(record)
        # No task stats -> lite mode.
        ctx.storage_adapter.get_extraction_task_stats.return_value = {}
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Extraction Pipeline" in result.output
        # Lite mode shows "Chunks" not "Groups".
        assert "Chunks" in result.output
        assert "44" in result.output and "22" in result.output
        # Per-chunk-only sections must be absent in lite mode.
        assert "Filtered" not in result.output
        assert "Invalid" not in result.output

    def test_filtering_log_missing_or_invalid_is_skipped(self) -> None:
        # Tasks whose filtering_log is absent / not a dict are skipped, but
        # the pipeline still renders from file-level final counts.
        record = _base_record(
            status="committed",
            extraction_entities_count=10,
            extraction_relationships_count=5,
        )
        ctx = _make_ctx(record)
        ctx.storage_adapter.get_extraction_task_stats.return_value = {
            "total_tasks": 2,
            "total_entities": 10,
            "total_relationships": 5,
            "total_invalid_relationships": 0,
            "total_retries": 0,
        }
        ctx.storage_adapter.get_extraction_tasks_filtering_logs.return_value = [
            {"filtering_log": None},
            {"filtering_log": "not-a-dict"},
            {},
        ]
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Extraction Pipeline" in result.output
        # No per-stage breakdown because every log entry was skipped.
        assert "Filtering stages" not in result.output

    def test_pipeline_skipped_when_no_data(self) -> None:
        record = _base_record(status="indexed")
        ctx = _make_ctx(record)
        ctx.storage_adapter.get_extraction_task_stats.return_value = {}
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Extraction Pipeline" not in result.output


# ---------------------------------------------------------------------------
# Stage progress section
# ---------------------------------------------------------------------------


class TestStageProgress:
    def test_active_stage_block_rendered(self) -> None:
        record = _base_record(
            status="indexing",
            stage_progress={
                "vision": {
                    "total": 184,
                    "processed": 47,
                    "avg_ms": 8200,
                    "completed_at": None,
                }
            },
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Vision processing" in result.output
        assert "47" in result.output and "184" in result.output

    def test_active_stage_at_full_count_omits_eta(self) -> None:
        # avg_ms present but processed == total (and not completed) -> no ETA.
        record = _base_record(
            status="indexing",
            stage_progress={
                "embedding": {
                    "total": 50,
                    "processed": 50,
                    "avg_ms": 1200,
                    "completed_at": None,
                }
            },
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Embedding" in result.output
        # avg suffix present, but no ETA "~..." remaining segment beyond avg.
        assert "1.2s avg" in result.output

    def test_completed_stage_not_rendered(self) -> None:
        record = _base_record(
            status="committed",
            stage_progress={
                "vision": {
                    "total": 100,
                    "processed": 100,
                    "avg_ms": 8200,
                    "completed_at": "2026-05-10T19:00:00Z",
                }
            },
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Vision processing" not in result.output


# ---------------------------------------------------------------------------
# LLM metrics + indexing stats footers
# ---------------------------------------------------------------------------


class TestLLMMetrics:
    def test_full_llm_metrics_block(self) -> None:
        record = _base_record(
            status="committed",
            llm_total_calls=20,
            llm_successful_calls=18,
            llm_failed_calls=2,
            llm_retry_calls=3,
            llm_total_input_tokens=10_000,
            llm_total_output_tokens=2_000,
            llm_wasted_tokens=500,
            llm_total_duration_ms=65_000,
            llm_avg_call_duration_ms=3250,
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "LLM Metrics" in result.output
        assert "Calls" in result.output
        assert "18/20" in result.output
        assert "retries" in result.output
        assert "failed" in result.output
        assert "Tokens" in result.output
        assert "wasted" in result.output
        assert "Input / Output" in result.output
        assert "Duration" in result.output
        assert "avg" in result.output

    def test_llm_metrics_clean_run_no_waste_no_failures(self) -> None:
        record = _base_record(
            status="committed",
            llm_total_calls=5,
            llm_successful_calls=5,
            llm_failed_calls=0,
            llm_retry_calls=0,
            llm_total_input_tokens=1_000,
            llm_total_output_tokens=200,
            llm_wasted_tokens=0,
            llm_total_duration_ms=3_000,
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "LLM Metrics" in result.output
        assert "5/5" in result.output
        assert "retries" not in result.output
        assert "failed" not in result.output
        assert "wasted" not in result.output

    def test_no_llm_block_when_zero_calls(self) -> None:
        ctx = _make_ctx(_base_record(llm_total_calls=0))
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "LLM Metrics" not in result.output


class TestIndexingStats:
    def test_indexing_stats_footer(self) -> None:
        record = _base_record(
            status="indexed",
            indexing_stats={"chunk_count": 42, "token_count": 13_500},
        )
        ctx = _make_ctx(record)
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Indexing Stats" in result.output
        assert "42" in result.output
        assert "13,500" in result.output

    def test_no_indexing_stats_when_absent(self) -> None:
        ctx = _make_ctx(_base_record())
        result = _invoke(ctx)
        assert result.exit_code == 0, result.output
        assert "Indexing Stats" not in result.output


# ---------------------------------------------------------------------------
# Pure helper unit coverage
# ---------------------------------------------------------------------------


class TestDurationHelpers:
    @pytest.mark.parametrize(
        ("ms", "expected"),
        [
            (None, "-"),
            (0, "-"),
            (-5, "-"),
            (450, "450ms"),
            (1_500, "1.5s"),
            (120_000, "2.0m"),
        ],
    )
    def test_format_duration(self, ms: int | float | None, expected: str) -> None:
        assert _format_duration(ms) == expected

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (45, "45s"),
            (90, "1m"),
            (3_600, "1h"),
            (3_600 + 15 * 60, "1h 15m"),
        ],
    )
    def test_format_duration_seconds(self, seconds: float, expected: str) -> None:
        assert _format_duration_seconds(seconds) == expected


def test_get_cmd_registered() -> None:
    assert get.name == "get"
    params = {p.name for p in get.params}
    assert "source_id" in params
    assert "database" in params
