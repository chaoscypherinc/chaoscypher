# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark result rows and JSON I/O - re-exported from Core.

The model and file format live in :mod:`chaoscypher_core.benchmark.results`
so the MCP benchmark bridge writes the same files the CLI reads.
"""

from chaoscypher_core.benchmark.results import (
    BENCHMARK_VERSION,
    BenchmarkResult,
    ScoreResult,
    dump_results,
    format_harness_settings,
    load_results,
)


__all__ = [
    "BENCHMARK_VERSION",
    "BenchmarkResult",
    "ScoreResult",
    "dump_results",
    "format_harness_settings",
    "load_results",
]
