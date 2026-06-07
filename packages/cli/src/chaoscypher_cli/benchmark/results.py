# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark result dataclasses and JSON I/O."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path


_ALLOWED_TOP_LEVEL_KEYS = {"results", "schema_version"}
_RESULTS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ScoreResult:
    """Output of a ``PackScorer.score()`` call.

    Attributes:
        headline_score: 0-100, comparable across pack kinds.
        metrics: Free-form per-kind detail (v7 breakdown for extraction;
            faithfulness etc. for chat in v2).
    """

    headline_score: float
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    """One row in the benchmark output: results of running one model on one dataset.

    All fields are required. Versions, seed and temperature are pinned per row
    so heterogeneous runs are detectable downstream.
    """

    model_id: str
    model_label: str
    dataset_id: str
    dataset_kind: str
    dataset_version: str
    dataset_source: str  # "builtin" | "user"
    config_name: str | None
    headline_score: float
    metrics: dict[str, Any]
    latency_ms_total: int
    latency_ms_per_chunk_p50: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    success: bool
    error: str | None
    timestamp: datetime
    benchmark_version: str
    scorer_version: int
    seed: int
    temperature: float


def _result_to_jsonable(r: BenchmarkResult) -> dict[str, Any]:
    """Convert a result to a JSON-safe dict (datetime to ISO string)."""
    d = asdict(r)
    d["timestamp"] = r.timestamp.isoformat()
    return d


def _result_from_jsonable(d: dict[str, Any]) -> BenchmarkResult:
    """Rebuild a BenchmarkResult from its JSON-safe dict form."""
    payload = dict(d)
    payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
    return BenchmarkResult(**payload)


def dump_results(results: list[BenchmarkResult], path: Path) -> None:
    """Write results to a JSON file with a stable top-level shape.

    Args:
        results: Result rows to serialize.
        path: Destination file. Parent directory must already exist.
    """
    payload = {
        "schema_version": _RESULTS_SCHEMA_VERSION,
        "results": [_result_to_jsonable(r) for r in results],
    }
    path.write_text(json.dumps(payload, indent=2))


def load_results(path: Path) -> list[BenchmarkResult]:
    """Load results from a JSON file written by :func:`dump_results`.

    Raises:
        ValueError: If the file's top-level shape contains unknown keys, or
            if the top-level value is not a JSON object.
    """
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        msg = "results file must be a JSON object, got array or other"
        raise TypeError(msg)
    unknown = set(payload.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        msg = f"unexpected top-level keys in results file: {unknown}"
        raise ValueError(msg)
    return [_result_from_jsonable(d) for d in payload["results"]]


__all__ = [
    "BenchmarkResult",
    "ScoreResult",
    "dump_results",
    "load_results",
]
