# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sequential (model, dataset) runner.

Iterates over every (model, dataset) pair, executes the dataset against the
model, scores the output, and emits a BenchmarkResult row. Failures are
captured as ``success=False`` rows - never raised - so one bad model
doesn't kill the run.
"""

from __future__ import annotations

import asyncio
import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from chaoscypher_cli.benchmark.models import ModelConfig, compute_cost
from chaoscypher_cli.benchmark.results import BENCHMARK_VERSION, BenchmarkResult


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from chaoscypher_cli.benchmark.dataset import BenchmarkDataset


logger = structlog.get_logger(__name__)


async def run_benchmark(
    models: list[ModelConfig],
    datasets: Sequence[BenchmarkDataset],
    *,
    config_name: str | None = None,
    seed: int = 42,
    temperature: float = 0.0,
    model_timeout: float | None = None,
    on_row: Callable[[BenchmarkResult], None] | None = None,
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
        model_timeout: Wall-clock cap in seconds for one (model, dataset) run.
            A run that exceeds it is recorded as a failed row and the loop
            moves on; without it one hung model stalls the whole sweep
            (2026-09-23: 75 minutes at 100% GPU with no output).
        on_row: Called with each row as soon as it exists, so a caller can
            persist incrementally instead of holding a sweep in memory.

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
            # Keep the run and the recorded row in agreement: the row below
            # records `seed`/`temperature`, so the dataset must actually use
            # them rather than its own defaults.
            for attr, value in (("seed", seed), ("temperature", temperature)):
                if hasattr(dataset, attr):
                    setattr(dataset, attr, value)
            row = await _run_one(
                model,
                dataset,
                config_name=config_name,
                seed=seed,
                temperature=temperature,
                timeout=model_timeout,
            )
            rows.append(row)
            if on_row is not None:
                on_row(row)
    return rows


async def _run_one(
    model: ModelConfig,
    dataset: BenchmarkDataset,
    *,
    config_name: str | None,
    seed: int,
    temperature: float,
    timeout: float | None = None,
) -> BenchmarkResult:
    """Execute one (model, dataset) pair, scoring the output.

    Catches any exception from ``dataset.run`` so the loop continues; records
    the exception in the failed row's ``error`` field.
    A ``timeout`` (seconds) bounds the run; exceeding it is one such failure.
    """
    timestamp = datetime.now(tz=UTC)
    try:
        raw = await asyncio.wait_for(dataset.run(model), timeout=timeout)
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
            thinking=getattr(dataset, "thinking", False),
            thinking_honoured=getattr(dataset, "thinking_honoured", None),
            chunks_truncated=0,
            chunks_aborted_by_loop=0,
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
            thinking=getattr(dataset, "thinking", False),
            thinking_honoured=getattr(dataset, "thinking_honoured", None),
            # `raw` exists on this path, so report what actually happened: an
            # empty extraction caused by truncation is the case most worth
            # seeing, and zeroing it here would hide it.
            chunks_truncated=raw.chunks_truncated,
            chunks_aborted_by_loop=raw.chunks_aborted_by_loop,
            extras=raw.extras,
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
        thinking=getattr(dataset, "thinking", False),
        thinking_honoured=getattr(dataset, "thinking_honoured", None),
        chunks_truncated=raw.chunks_truncated,
        chunks_aborted_by_loop=raw.chunks_aborted_by_loop,
        extras=raw.extras,
    )


def _p50(values: list[int]) -> int:
    """Median of an int list, 0 for empty."""
    if not values:
        return 0
    return int(statistics.median(values))


__all__ = ["BENCHMARK_VERSION", "run_benchmark"]
