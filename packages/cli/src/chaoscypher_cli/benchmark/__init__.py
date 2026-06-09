# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ChaosCypher Extraction Benchmark engine.

Public API: dataset/scorer protocols, the ExtractionDataset and
V7ExtractionScorer v1 implementations, model config + registry-backed
pricing, the sequential runner, named-config loader, dataset discovery
(built-in + user overlay), and leaderboard rendering. Result types and
JSON I/O live in ``results``.

Vocabulary:
    dataset - the test unit (corpus + metadata + how to evaluate it).
    corpus - the body of text inside a dataset.
    config - a runnable benchmark recipe (name, params, dataset ids,
        models). Loaded by ``bench run [NAME]``.
"""

from __future__ import annotations

from chaoscypher_cli.benchmark.config import (
    DEFAULT_CONFIG_NAME,
    BenchmarkConfig,
    list_configs,
    load_config,
)
from chaoscypher_cli.benchmark.dataset import (
    BenchmarkDataset,
    DatasetScorer,
    DatasetSource,
    RawOutput,
)
from chaoscypher_cli.benchmark.discovery import (
    builtin_dataset_root,
    discover_datasets,
    user_benchmark_root,
    user_dataset_root,
)
from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset
from chaoscypher_cli.benchmark.leaderboard import (
    ModelAggregate,
    aggregate_by_model,
    render_leaderboard,
)
from chaoscypher_cli.benchmark.models import (
    ModelConfig,
    compute_cost,
    filter_models,
)
from chaoscypher_cli.benchmark.results import (
    BenchmarkResult,
    ScoreResult,
    dump_results,
    load_results,
)
from chaoscypher_cli.benchmark.runner import BENCHMARK_VERSION, run_benchmark
from chaoscypher_cli.benchmark.scorers.v7 import V7ExtractionScorer


__version__ = "0.1.0"

__all__ = [
    "BENCHMARK_VERSION",
    "DEFAULT_CONFIG_NAME",
    "BenchmarkConfig",
    "BenchmarkDataset",
    "BenchmarkResult",
    "DatasetScorer",
    "DatasetSource",
    "ExtractionDataset",
    "ModelAggregate",
    "ModelConfig",
    "RawOutput",
    "ScoreResult",
    "V7ExtractionScorer",
    "__version__",
    "aggregate_by_model",
    "builtin_dataset_root",
    "compute_cost",
    "discover_datasets",
    "dump_results",
    "filter_models",
    "list_configs",
    "load_config",
    "load_results",
    "render_leaderboard",
    "run_benchmark",
    "user_benchmark_root",
    "user_dataset_root",
]
