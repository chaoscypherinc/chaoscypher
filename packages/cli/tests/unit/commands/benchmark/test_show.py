# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for `chaoscypher benchmark show`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from chaoscypher_cli.benchmark.results import BenchmarkResult, dump_results
from chaoscypher_cli.commands.benchmark.show import show


def _row() -> BenchmarkResult:
    return BenchmarkResult(
        model_id="ollama/m",
        model_label="M",
        dataset_id="p1",
        dataset_kind="extraction",
        dataset_version="1.0",
        dataset_source="builtin",
        config_name="extraction",
        headline_score=70.0,
        metrics={},
        latency_ms_total=5000,
        latency_ms_per_chunk_p50=1000,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0,
        success=True,
        error=None,
        timestamp=datetime(2026, 4, 28, tzinfo=UTC),
        benchmark_version="1.0",
        scorer_version=7,
        seed=42,
        temperature=0.0,
    )


def test_show_renders_results_file(tmp_path: Path):
    src = tmp_path / "results.json"
    dump_results([_row()], src)
    runner = CliRunner()
    result = runner.invoke(show, [str(src)])
    assert result.exit_code == 0, result.output
    assert "ChaosCypher Extraction Benchmark" in result.output
    assert "M" in result.output


def test_show_writes_to_output_file_when_requested(tmp_path: Path):
    src = tmp_path / "results.json"
    dst = tmp_path / "out.md"
    dump_results([_row()], src)
    runner = CliRunner()
    result = runner.invoke(show, [str(src), "--out", str(dst)])
    assert result.exit_code == 0
    assert dst.exists()
    assert "ChaosCypher" in dst.read_text()
