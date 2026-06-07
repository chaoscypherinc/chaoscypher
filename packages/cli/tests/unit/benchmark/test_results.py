# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for benchmark result dataclasses + JSON I/O."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from chaoscypher_cli.benchmark.results import (
    BenchmarkResult,
    ScoreResult,
    dump_results,
    load_results,
)


def _sample_result() -> BenchmarkResult:
    return BenchmarkResult(
        model_id="ollama/llama3.1:8b",
        model_label="Llama 3.1 8B",
        dataset_id="war_and_peace_tiny",
        dataset_kind="extraction",
        dataset_version="1.0",
        dataset_source="builtin",
        config_name="extraction",
        headline_score=72.5,
        metrics={"avg_entity_quality": 70.1, "topology_score": 65.0},
        latency_ms_total=12345,
        latency_ms_per_chunk_p50=850,
        input_tokens=4200,
        output_tokens=1800,
        cost_usd=0.0,
        success=True,
        error=None,
        timestamp=datetime(2026, 4, 28, 14, 30, tzinfo=UTC),
        benchmark_version="1.0",
        scorer_version=7,
        seed=42,
        temperature=0.0,
    )


def test_benchmark_result_roundtrip_json(tmp_path):
    result = _sample_result()
    out = tmp_path / "results.json"
    dump_results([result], out)
    loaded = load_results(out)
    assert len(loaded) == 1
    assert loaded[0] == result


def test_benchmark_result_failed_run_has_error():
    failed = BenchmarkResult(
        model_id="ollama/broken",
        model_label="Broken",
        dataset_id="war_and_peace_tiny",
        dataset_kind="extraction",
        dataset_version="1.0",
        dataset_source="builtin",
        config_name=None,
        headline_score=0.0,
        metrics={},
        latency_ms_total=0,
        latency_ms_per_chunk_p50=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        success=False,
        error="timeout",
        timestamp=datetime.now(tz=UTC),
        benchmark_version="1.0",
        scorer_version=7,
        seed=42,
        temperature=0.0,
    )
    assert failed.success is False
    assert failed.error == "timeout"


def test_score_result_holds_metrics():
    s = ScoreResult(headline_score=75.0, metrics={"avg_entity_quality": 70.0})
    assert s.headline_score == 75.0
    assert s.metrics["avg_entity_quality"] == 70.0


def test_dump_results_writes_object_with_results_array(tmp_path):
    """The serialized form must be a JSON object with a 'results' array,
    not a top-level array - leaves room for run-level metadata later.
    """
    out = tmp_path / "r.json"
    dump_results([_sample_result()], out)
    payload = json.loads(out.read_text())
    assert isinstance(payload, dict)
    assert "results" in payload
    assert isinstance(payload["results"], list)


def test_load_results_rejects_unknown_top_level_keys(tmp_path):
    """Strict schema: unexpected top-level keys raise."""
    out = tmp_path / "bad.json"
    out.write_text(json.dumps({"results": [], "mystery": 1}))
    with pytest.raises(ValueError, match="unexpected"):
        load_results(out)


def test_load_results_rejects_top_level_array(tmp_path):
    """A top-level JSON array (older shape) should be rejected, not silently accepted."""
    out = tmp_path / "bad.json"
    out.write_text(json.dumps([]))
    with pytest.raises(TypeError, match="object"):
        load_results(out)
