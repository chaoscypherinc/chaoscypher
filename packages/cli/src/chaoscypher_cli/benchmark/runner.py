# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sequential (model, dataset) runner.

Iterates over every (model, dataset) pair, executes the dataset against the
model, scores the output, and emits a BenchmarkResult row. Failures are
captured as ``success=False`` rows - never raised - so one bad model
doesn't kill the run.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from chaoscypher_cli.benchmark.models import ModelConfig, compute_cost
from chaoscypher_cli.benchmark.results import BenchmarkResult


if TYPE_CHECKING:
    from collections.abc import Sequence

    from chaoscypher_cli.benchmark.dataset import BenchmarkDataset


logger = structlog.get_logger(__name__)


BENCHMARK_VERSION = "2.0"


async def run_benchmark(
    models: list[ModelConfig],
    datasets: Sequence[BenchmarkDataset],
    *,
    config_name: str | None = None,
    seed: int = 42,
    temperature: float = 0.0,
) -> list[BenchmarkResult]:
    """Run every (model, dataset) pair sequentially and return result rows.

    Args:
        models: Model candidates to evaluate.
        datasets: Datasets to evaluate against.
        config_name: Name of the config that selected this set, if any.
            Surfaced on each row so leaderboards can label themselves.
        seed: Pinned in every result row; used by the dataset runtime where
            the provider supports it.
        temperature: Pinned in every result row.

    Returns:
        List of BenchmarkResult rows in (model, dataset) iteration order.
        Failed runs are included with ``success=False``.
    """
    rows: list[BenchmarkResult] = []
    for model in models:
        for dataset in datasets:
            # Skip models whose kinds field excludes this dataset's kind.
            if model.kinds is not None and dataset.kind not in model.kinds:
                logger.info(
                    "model_skipped_by_kinds",
                    model_id=model.model_id,
                    dataset_id=dataset.id,
                    dataset_kind=dataset.kind,
                    model_kinds=model.kinds,
                )
                continue
            row = await _run_one(
                model,
                dataset,
                config_name=config_name,
                seed=seed,
                temperature=temperature,
            )
            rows.append(row)
    return rows


async def _run_one(
    model: ModelConfig,
    dataset: BenchmarkDataset,
    *,
    config_name: str | None,
    seed: int,
    temperature: float,
) -> BenchmarkResult:
    """Execute one (model, dataset) pair, scoring the output.

    Catches any exception from ``dataset.run`` so the loop continues; records
    the exception in the failed row's ``error`` field.
    """
    timestamp = datetime.now(tz=UTC)
    try:
        raw = await dataset.run(model)
    except Exception as exc:
        logger.exception(
            "dataset_run_raised",
            dataset_id=dataset.id,
            model_id=model.model_id,
            error_type=type(exc).__name__,
        )
        return BenchmarkResult(
            model_id=model.model_id,
            model_label=model.label,
            dataset_id=dataset.id,
            dataset_kind=dataset.kind,
            dataset_version=dataset.version,
            dataset_source=dataset.source,
            config_name=config_name,
            headline_score=0.0,
            metrics={},
            latency_ms_total=0,
            latency_ms_per_chunk_p50=0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            timestamp=timestamp,
            benchmark_version=BENCHMARK_VERSION,
            scorer_version=dataset.scorer.version,
            seed=seed,
            temperature=temperature,
        )

    if raw.error is not None:
        # Dataset ran without raising but reported failure (e.g. empty_extraction).
        cost = compute_cost(model, input_tokens=raw.input_tokens, output_tokens=raw.output_tokens)
        return BenchmarkResult(
            model_id=model.model_id,
            model_label=model.label,
            dataset_id=dataset.id,
            dataset_kind=dataset.kind,
            dataset_version=dataset.version,
            dataset_source=dataset.source,
            config_name=config_name,
            headline_score=0.0,
            metrics={},
            latency_ms_total=raw.latency_ms,
            latency_ms_per_chunk_p50=_p50(raw.per_chunk_latency_ms),
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cost_usd=cost if cost is not None else 0.0,
            success=False,
            error=raw.error,
            timestamp=timestamp,
            benchmark_version=BENCHMARK_VERSION,
            scorer_version=dataset.scorer.version,
            seed=seed,
            temperature=temperature,
        )

    score = dataset.scorer.score(raw, dataset.fixture)
    cost = compute_cost(model, input_tokens=raw.input_tokens, output_tokens=raw.output_tokens)
    return BenchmarkResult(
        model_id=model.model_id,
        model_label=model.label,
        dataset_id=dataset.id,
        dataset_kind=dataset.kind,
        dataset_version=dataset.version,
        dataset_source=dataset.source,
        config_name=config_name,
        headline_score=score.headline_score,
        metrics=score.metrics,
        latency_ms_total=raw.latency_ms,
        latency_ms_per_chunk_p50=_p50(raw.per_chunk_latency_ms),
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cost_usd=cost if cost is not None else 0.0,
        success=True,
        error=None,
        timestamp=timestamp,
        benchmark_version=BENCHMARK_VERSION,
        scorer_version=dataset.scorer.version,
        seed=seed,
        temperature=temperature,
    )


def _p50(values: list[int]) -> int:
    """Median of an int list, 0 for empty."""
    if not values:
        return 0
    return int(statistics.median(values))


__all__ = ["BENCHMARK_VERSION", "run_benchmark"]
