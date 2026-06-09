# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for composite Overall scoring (pure functions + extractor join)."""

from __future__ import annotations

from datetime import UTC, datetime

from chaoscypher_cli.benchmark.composite import (
    CompositeWeights,
    DimensionScores,
    compute_extractor_composites,
    normalize_cost,
    normalize_speed,
    weighted_overall,
)
from chaoscypher_cli.benchmark.results import BenchmarkResult


def test_normalize_speed_anchors():
    assert normalize_speed(500) == 100.0  # FAST_MS -> 100
    assert normalize_speed(30_000) == 0.0  # SLOW_MS -> 0
    assert normalize_speed(0) is None  # unmeasured


def test_normalize_cost_anchors():
    assert normalize_cost(0.0) == 100.0  # free -> 100
    assert normalize_cost(999.0) == 0.0  # past MAX_USD -> clamped 0


def test_weighted_overall_renormalizes_missing_dims():
    dims = DimensionScores(extraction=80.0, retrieval=None, chat=None, speed=None, cost=None)
    overall, basis = weighted_overall(dims, CompositeWeights())
    assert overall == 80.0  # only extraction present -> = its score
    assert basis == ["extraction"]


def test_weighted_overall_full():
    dims = DimensionScores(extraction=90, retrieval=80, chat=70, speed=60, cost=50)
    overall, basis = weighted_overall(dims, CompositeWeights())
    # 0.40*90 + 0.20*80 + 0.20*70 + 0.10*60 + 0.10*50 = 36+16+14+6+5 = 77.0
    # (default weights sum to 1.0, so renormalization is a no-op here)
    assert abs(overall - 77.0) < 1e-9
    assert basis == ["extraction", "retrieval", "chat", "speed", "cost"]


def test_weighted_overall_no_dims_returns_zero():
    dims = DimensionScores(extraction=None, retrieval=None, chat=None, speed=None, cost=None)
    overall, basis = weighted_overall(dims, CompositeWeights())
    assert overall == 0.0
    assert basis == []


# ---------------------------------------------------------------------------
# Join test: compute_extractor_composites over hand-built BenchmarkResult rows
# ---------------------------------------------------------------------------


def _row(
    *,
    model_id: str,
    model_label: str,
    dataset_kind: str,
    headline_score: float,
    metrics: dict | None = None,
    latency_ms_per_chunk_p50: int = 0,
    cost_usd: float = 0.0,
    success: bool = True,
) -> BenchmarkResult:
    return BenchmarkResult(
        model_id=model_id,
        model_label=model_label,
        dataset_id="d",
        dataset_kind=dataset_kind,
        dataset_version="1.0",
        dataset_source="builtin",
        config_name="test",
        headline_score=headline_score,
        metrics=metrics or {},
        latency_ms_total=0,
        latency_ms_per_chunk_p50=latency_ms_per_chunk_p50,
        input_tokens=0,
        output_tokens=0,
        cost_usd=cost_usd,
        success=success,
        error=None,
        timestamp=datetime.now(tz=UTC),
        benchmark_version="2.0",
        scorer_version=7,
        seed=42,
        temperature=0.0,
    )


def test_compute_extractor_composites_join_populates_all_dims():
    rows = [
        _row(
            model_id="ollama/ext",
            model_label="Extractor",
            dataset_kind="extraction",
            headline_score=90.0,
            latency_ms_per_chunk_p50=500,  # -> speed 100
            cost_usd=0.0,  # -> cost 100
        ),
        _row(
            model_id="ollama/emb",
            model_label="Embedder",
            dataset_kind="embedding",
            headline_score=80.0,
            metrics={"extractor_id": "ollama/ext"},
        ),
        _row(
            model_id="ollama/chat",
            model_label="Chat",
            dataset_kind="chat",
            headline_score=70.0,
            metrics={"extractor_id": "ollama/ext", "embedder_id": "ollama/emb"},
        ),
    ]
    comps = compute_extractor_composites(
        rows,
        default_embedder="ollama/emb",
        default_chat="ollama/chat",
        weights=CompositeWeights(),
    )
    assert len(comps) == 1
    c = comps[0]
    assert c.model_id == "ollama/ext"
    assert c.model_label == "Extractor"
    assert c.dims.extraction == 90.0
    assert c.dims.retrieval == 80.0
    assert c.dims.chat == 70.0
    assert c.dims.speed == 100.0
    assert c.dims.cost == 100.0
    # 0.40*90 + 0.20*80 + 0.20*70 + 0.10*100 + 0.10*100 = 86.0
    assert abs(c.overall - 86.0) < 1e-9
    assert c.basis == ["extraction", "retrieval", "chat", "speed", "cost"]


def test_compute_extractor_composites_sorts_by_overall_desc():
    rows = [
        _row(
            model_id="ollama/low",
            model_label="Low",
            dataset_kind="extraction",
            headline_score=40.0,
        ),
        _row(
            model_id="ollama/high",
            model_label="High",
            dataset_kind="extraction",
            headline_score=95.0,
        ),
    ]
    comps = compute_extractor_composites(
        rows, default_embedder=None, default_chat=None, weights=None
    )
    assert [c.model_id for c in comps] == ["ollama/high", "ollama/low"]


def test_compute_extractor_composites_missing_downstream_drops_dims():
    """No embedding/chat rows -> retrieval/chat are None and drop out of Overall."""
    rows = [
        _row(
            model_id="ollama/ext",
            model_label="Extractor",
            dataset_kind="extraction",
            headline_score=80.0,
            latency_ms_per_chunk_p50=0,  # unmeasured -> speed None
            cost_usd=0.0,
        ),
    ]
    comps = compute_extractor_composites(
        rows, default_embedder="ollama/emb", default_chat="ollama/chat", weights=None
    )
    assert len(comps) == 1
    c = comps[0]
    assert c.dims.retrieval is None
    assert c.dims.chat is None
    assert c.dims.speed is None
    # cost present (free -> 100); extraction present. basis is those two.
    assert set(c.basis) == {"extraction", "cost"}


def test_weighted_overall_custom_weights_excludes_zero_weight_dims_from_basis():
    """Zero-weighted dims must not appear in basis even when their score is present.

    Uses retrieval=1.0 as the only non-zero weight.  overall must equal the
    retrieval score exactly (renormalized weight = 1), and basis must contain
    only "retrieval" — proving Fix 1: extraction (score present, weight 0) is
    excluded.
    """
    dims = DimensionScores(extraction=60.0, retrieval=85.0, chat=None, speed=None, cost=None)
    custom = CompositeWeights(extraction=0.0, retrieval=1.0, chat=0.0, speed=0.0, cost=0.0)
    overall, basis = weighted_overall(dims, custom)
    assert overall == 85.0  # only retrieval contributes
    assert basis == ["retrieval"]  # extraction excluded despite having a score
