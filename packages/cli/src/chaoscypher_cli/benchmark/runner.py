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
from chaoscypher_core.benchmark.results import (
    MODEL_BUILD_CHANGED,
    is_timeout_error,
    provider_honours_seed,
    stamp_cache_run_setting,
)
from chaoscypher_core.cache.call_site import DEFAULT_CALL_SITE, call_site


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from typing import Any

    from chaoscypher_cli.benchmark.dataset import BenchmarkDataset

    ModelBuildFetcher = Callable[[ModelConfig], Awaitable[dict[str, Any] | None]]


logger = structlog.get_logger(__name__)

DATASET_CALL_SITES: Mapping[str, str] = {
    "extraction": "extraction",
    "probes": "extraction",
    "chat": "chat",
}
"""The LLM-cache call site each dataset kind's model calls count under (the chat stage's judge
sets its own, ``judge``; the embedding stage's index and query embeddings set theirs)."""


async def fetch_model_build(model: ModelConfig) -> dict[str, Any] | None:
    """What the server says about the build a row's model ran: ``extras["model_build"]``.

    An Ollama model gets :func:`~chaoscypher_core.adapters.llm.providers.ollama_provider.ollama_model_build`
    against the configured Ollama (the URL the datasets call), with the
    per-card options the provider sends (``ollama_runtime_options`` of the
    run's settings): the model digest, the server version, the Modelfile
    defaults and ``num_batch``/``num_thread``/``num_parallel`` as requested,
    with ``options_sent`` saying which of them reached the model
    (``ChatOllama`` drops ``num_batch`` and ``num_parallel``:
    ``OLLAMA_OPTIONS_NOT_SENT``, audit wave 5). A cloud model
    reports none of them: None. Audit wave 4 (2026-10-06): an ``ollama pull``
    between a baseline and a variant changed the weights with no row field
    moving.
    """
    if model.provider != "ollama":
        return None
    from chaoscypher_core.adapters.llm.providers.ollama_provider import (
        ollama_model_build,
        ollama_runtime_options_record,
    )
    from chaoscypher_core.app_config import get_settings

    llm = get_settings().llm
    record = ollama_runtime_options_record(llm)
    build = await ollama_model_build(
        llm.primary_ollama_url,
        model.model,
        options={name: entry["value"] for name, entry in record.items()},
    )
    # requested is not sent: ChatOllama drops num_batch and num_parallel (audit wave 5)
    build["options_sent"] = {name: entry["sent"] for name, entry in record.items()}
    return build


async def run_benchmark(
    models: list[ModelConfig],
    datasets: Sequence[BenchmarkDataset],
    *,
    config_name: str | None = None,
    seed: int = 42,
    temperature: float = 0.0,
    model_timeout: float | None = None,
    on_row: Callable[[BenchmarkResult], None] | None = None,
    model_build: ModelBuildFetcher | None = None,
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
        model_build: Reports the model's build for ``extras["model_build"]``
            on every row, failed ones included (default
            :func:`fetch_model_build`, read when the run starts so a test can
            replace it); asked per (model, dataset) row before it runs and
            again after (a build that moved is recorded as
            ``extras["model_build_changed"]``, the build after, audit wave 6),
            and for a judged dataset once more for its judge
            (``extras["judge_model_build"]``, a comparability field).

    Returns:
        List of BenchmarkResult rows in (model, dataset) iteration order.
        Failed runs are included with ``success=False``.
    """
    rows: list[BenchmarkResult] = []
    fetch = model_build if model_build is not None else fetch_model_build
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
                if not hasattr(dataset, attr):
                    # Loud: a dataset without the attribute would run on its own
                    # defaults while the row records these (the chat dataset did,
                    # until 2026-10-06).
                    msg = f"{type(dataset).__name__} cannot pin {attr}; its rows would misreport it"
                    raise AttributeError(msg)
                setattr(dataset, attr, value)
            build = await fetch(model)
            row = await _run_one(
                model,
                dataset,
                config_name=config_name,
                seed=seed,
                temperature=temperature,
                timeout=model_timeout,
            )
            # every row, chat and extraction, failed or scored: the build it ran on
            row.extras = {**(row.extras or {}), "model_build": build}
            # and the caches it ran with (informational: a hit is the same answer)
            stamp_cache_run_setting(row.extras, _run_cache_settings())
            # asked again after the row (audit wave 6): an `ollama pull` mid-row
            # is two builds in one row, which the comparability findings flag
            after = await fetch(model)
            if after != build:
                row.extras[MODEL_BUILD_CHANGED] = after
            judge = getattr(dataset, "judge", None)
            if isinstance(judge, ModelConfig):
                # a judged row's verdicts depend on the judge's weights too (audit wave 5)
                row.extras["judge_model_build"] = await fetch(judge)
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
        with call_site(DATASET_CALL_SITES.get(dataset.kind, DEFAULT_CALL_SITE)):
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
            seed_honoured=provider_honours_seed(model.provider),
            chunks_truncated=0,
            chunks_aborted_by_loop=0,
            # A timed-out row is not a scored row: the exporter and the panel tools
            # refuse it by this flag rather than by parsing the error text (audit H8).
            extras={"timed_out": is_timeout_error(exc), "timeout_s": timeout},
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
            seed_honoured=provider_honours_seed(model.provider),
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
        seed_honoured=provider_honours_seed(model.provider),
        chunks_truncated=raw.chunks_truncated,
        chunks_aborted_by_loop=raw.chunks_aborted_by_loop,
        extras=raw.extras,
    )


def _run_cache_settings() -> Any:
    """The run's cache settings (``benchmark run --replay`` sets ``require_hit`` on them)."""
    from chaoscypher_core.app_config import get_settings

    return get_settings().cache


def _p50(values: list[int]) -> int:
    """Median of an int list, 0 for empty."""
    if not values:
        return 0
    return int(statistics.median(values))


__all__ = ["BENCHMARK_VERSION", "DATASET_CALL_SITES", "fetch_model_build", "run_benchmark"]
