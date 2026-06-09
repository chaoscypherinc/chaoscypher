# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for leaderboard rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from chaoscypher_cli.benchmark.leaderboard import (
    aggregate_by_model,
    render_leaderboard,
)
from chaoscypher_cli.benchmark.results import BenchmarkResult


def _row(
    *,
    model_id: str,
    model_label: str,
    dataset_id: str,
    headline_score: float,
    success: bool = True,
    error: str | None = None,
    latency_ms_per_chunk_p50: int = 1000,
    cost_usd: float = 0.0,
    dataset_version: str = "1.0",
    dataset_source: str = "builtin",
    config_name: str | None = "extraction",
    scorer_version: int = 7,
) -> BenchmarkResult:
    return BenchmarkResult(
        model_id=model_id,
        model_label=model_label,
        dataset_id=dataset_id,
        dataset_kind="extraction",
        dataset_version=dataset_version,
        dataset_source=dataset_source,
        config_name=config_name,
        headline_score=headline_score,
        metrics={},
        latency_ms_total=latency_ms_per_chunk_p50 * 5,
        latency_ms_per_chunk_p50=latency_ms_per_chunk_p50,
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost_usd,
        success=success,
        error=error,
        timestamp=datetime(2026, 4, 28, tzinfo=UTC),
        benchmark_version="1.0",
        scorer_version=scorer_version,
        seed=42,
        temperature=0.0,
    )


def test_aggregate_by_model_means_only_successful_datasets():
    rows = [
        _row(model_id="ollama/m", model_label="M", dataset_id="p1", headline_score=80),
        _row(model_id="ollama/m", model_label="M", dataset_id="p2", headline_score=60),
        _row(
            model_id="ollama/m",
            model_label="M",
            dataset_id="p3",
            headline_score=0,
            success=False,
            error="timeout",
        ),
    ]
    agg = aggregate_by_model(rows)
    assert len(agg) == 1
    assert agg[0].model_id == "ollama/m"
    assert agg[0].mean_score == 70.0  # mean of 80, 60 (p3 excluded)
    assert agg[0].failed_datasets == ["p3"]


def test_aggregate_by_model_failed_all_datasets_listed_with_none_mean():
    rows = [
        _row(
            model_id="ollama/m",
            model_label="M",
            dataset_id="p1",
            headline_score=0,
            success=False,
            error="x",
        ),
    ]
    agg = aggregate_by_model(rows)
    assert agg[0].mean_score is None
    assert agg[0].failed_datasets == ["p1"]


def test_render_leaderboard_ranks_by_quality_descending():
    rows = [
        _row(model_id="ollama/a", model_label="A", dataset_id="p1", headline_score=70),
        _row(model_id="ollama/b", model_label="B", dataset_id="p1", headline_score=80),
        _row(model_id="ollama/c", model_label="C", dataset_id="p1", headline_score=75),
    ]
    md = render_leaderboard(rows)
    # Order should be B (80), C (75), A (70).
    pos_a = md.find("| A ")
    pos_b = md.find("| B ")
    pos_c = md.find("| C ")
    assert pos_b < pos_c < pos_a


def test_render_leaderboard_includes_run_metadata_header():
    rows = [_row(model_id="ollama/m", model_label="M", dataset_id="p1", headline_score=70)]
    md = render_leaderboard(rows)
    assert "Benchmark v1.0" in md
    assert "Scorer v7" in md
    assert "1 datasets" in md or "1 dataset" in md
    assert "1 models" in md or "1 model" in md
    assert "temp=0" in md
    assert "seed=42" in md


def test_render_leaderboard_warns_on_dataset_version_mismatch():
    rows = [
        _row(
            model_id="ollama/m",
            model_label="M",
            dataset_id="p1",
            headline_score=70,
            dataset_version="1.0",
        ),
        _row(
            model_id="ollama/m2",
            model_label="M2",
            dataset_id="p1",
            headline_score=70,
            dataset_version="2.0",
        ),
    ]
    md = render_leaderboard(rows)
    assert "heterogeneous" in md.lower()
    assert "p1" in md


def test_render_leaderboard_per_dataset_drilldown_present():
    rows = [
        _row(model_id="ollama/a", model_label="A", dataset_id="p1", headline_score=80),
        _row(model_id="ollama/a", model_label="A", dataset_id="p2", headline_score=60),
    ]
    md = render_leaderboard(rows)
    assert "Per-dataset scores" in md
    assert "p1" in md and "p2" in md


def test_render_leaderboard_failed_models_listed_in_did_not_complete():
    rows = [
        _row(
            model_id="ollama/broken",
            model_label="Broken",
            dataset_id="p1",
            headline_score=0,
            success=False,
            error="empty_extraction",
        ),
    ]
    md = render_leaderboard(rows)
    assert "did not complete" in md.lower()
    assert "Broken" in md


def test_render_leaderboard_user_overlay_warning():
    """When any dataset has source='user', the renderer surfaces a note."""
    rows = [
        _row(
            model_id="ollama/m",
            model_label="M",
            dataset_id="my_user_dataset",
            headline_score=70,
            dataset_source="user",
        ),
    ]
    md = render_leaderboard(rows)
    assert "user-overlay" in md.lower() or "user" in md.lower()
    assert "my_user_dataset" in md


def test_render_leaderboard_empty_input():
    md = render_leaderboard([])
    assert "No results" in md


# ---------------------------------------------------------------------------
# _result_row helper — extended constructor used by multi-kind tests
# ---------------------------------------------------------------------------


def _result_row(
    *,
    model_id: str = "ollama/test",
    model_label: str = "Test",
    dataset_id: str = "demo",
    dataset_kind: str = "extraction",
    dataset_version: str = "1.0",
    dataset_source: str = "builtin",
    config_name: str | None = "test",
    headline_score: float = 0.0,
    metrics: dict | None = None,
    cost_usd: float = 0.0,
    success: bool = True,
) -> BenchmarkResult:
    return BenchmarkResult(
        model_id=model_id,
        model_label=model_label,
        dataset_id=dataset_id,
        dataset_kind=dataset_kind,
        dataset_version=dataset_version,
        dataset_source=dataset_source,
        config_name=config_name,
        headline_score=headline_score,
        metrics=metrics or {},
        latency_ms_total=0,
        latency_ms_per_chunk_p50=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=cost_usd,
        success=success,
        error=None,
        timestamp=datetime.now(tz=UTC),
        benchmark_version="1.0",
        scorer_version=1,
        seed=42,
        temperature=0.0,
    )


def test_render_emits_three_sections_when_all_kinds_present():
    rows = [
        _result_row(dataset_kind="extraction", model_label="Llama", headline_score=80.0),
        _result_row(
            dataset_kind="embedding",
            model_label="nomic",
            headline_score=72.0,
            metrics={
                "mrr": 0.72,
                "recall_at_1": 0.6,
                "recall_at_3": 0.85,
                "queries_unresolved": 1,
                "queries_scored": 49,
            },
        ),
        _result_row(
            dataset_kind="chat",
            model_label="GPT-4o-mini",
            headline_score=85.0,
            metrics={
                "faithfulness_avg": 4.5,
                "correctness_avg": 4.0,
                "refusal_correct_rate": 0.8,
                "judge_provider": "anthropic",
                "judge_model": "claude-opus-4-7",
            },
        ),
    ]
    md = render_leaderboard(rows)
    assert "## Extraction Leaderboard" in md
    assert "## Embedding Leaderboard" in md
    assert "## Chat Leaderboard" in md
    assert "Llama" in md and "nomic" in md and "GPT-4o-mini" in md
    assert "claude-opus-4-7" in md  # judge identity surfaced


def test_render_extraction_only_omits_other_sections():
    rows = [_result_row(dataset_kind="extraction", model_label="Llama", headline_score=80.0)]
    md = render_leaderboard(rows)
    assert "## Extraction Leaderboard" in md
    assert "## Embedding Leaderboard" not in md
    assert "## Chat Leaderboard" not in md


# ---------------------------------------------------------------------------
# Unified Overall leaderboard section (composite)
# ---------------------------------------------------------------------------


def _overall_fixture_rows() -> list[BenchmarkResult]:
    """Two extractors A/B with matching embedding+chat rows; A outscores B."""
    return [
        # Extraction rows (model_id == extractor).
        _result_row(
            model_id="ollama/a",
            model_label="Model A",
            dataset_kind="extraction",
            headline_score=90.0,
        ),
        _result_row(
            model_id="ollama/b",
            model_label="Model B",
            dataset_kind="extraction",
            headline_score=50.0,
        ),
        # Embedding rows for default embedder ollama/emb, stamped by extractor.
        _result_row(
            model_id="ollama/emb",
            model_label="Emb",
            dataset_kind="embedding",
            headline_score=85.0,
            metrics={"extractor_id": "ollama/a"},
        ),
        _result_row(
            model_id="ollama/emb",
            model_label="Emb",
            dataset_kind="embedding",
            headline_score=45.0,
            metrics={"extractor_id": "ollama/b"},
        ),
        # Chat rows for default chat ollama/c, stamped by extractor+embedder.
        _result_row(
            model_id="ollama/c",
            model_label="Chat",
            dataset_kind="chat",
            headline_score=80.0,
            metrics={"extractor_id": "ollama/a", "embedder_id": "ollama/emb"},
        ),
        _result_row(
            model_id="ollama/c",
            model_label="Chat",
            dataset_kind="chat",
            headline_score=40.0,
            metrics={"extractor_id": "ollama/b", "embedder_id": "ollama/emb"},
        ),
    ]


def test_render_overall_section_present_and_ranked():
    from chaoscypher_cli.benchmark.composite import CompositeWeights

    rows = _overall_fixture_rows()
    md = render_leaderboard(
        rows,
        default_embedder="ollama/emb",
        default_chat="ollama/c",
        weights=CompositeWeights(),
    )
    assert "## Overall Leaderboard" in md
    assert "| Rank | Model | Overall | Extraction | Retrieval | Chat | Speed | Cost |" in md
    # Higher-overall model (A) appears before B.
    assert md.index("Model A") < md.index("Model B")


def test_render_overall_section_renders_config_less():
    """`render_leaderboard(rows)` with no kwargs still emits the Overall section.

    Used by `bench show`, which has no config. Retrieval/chat fall to '-'
    because default_embedder/default_chat are None, but the section renders
    whenever extraction rows are present.
    """
    rows = [_result_row(dataset_kind="extraction", model_label="Llama", headline_score=80.0)]
    md = render_leaderboard(rows)
    assert isinstance(md, str)
    assert "## Overall Leaderboard" in md


def test_header_pins_composite_version_and_weights_default():
    rows = [_result_row(dataset_kind="extraction", model_label="Llama", headline_score=80.0)]
    md = render_leaderboard(rows)
    # Default weights baked in when no weights config is threaded through.
    assert "composite v1" in md
    assert "weights extraction=0.40" in md
    assert "retrieval=0.20" in md
    assert "chat=0.20" in md
    assert "speed=0.10" in md
    assert "cost=0.10" in md


def test_header_pins_custom_weights():
    from chaoscypher_cli.benchmark.composite import CompositeWeights

    rows = [_result_row(dataset_kind="extraction", model_label="Llama", headline_score=80.0)]
    md = render_leaderboard(
        rows,
        weights=CompositeWeights(extraction=0.5, retrieval=0.2, chat=0.2, speed=0.05, cost=0.05),
    )
    assert "composite v1" in md
    assert "weights extraction=0.50" in md
    assert "speed=0.05" in md
    assert "cost=0.05" in md
