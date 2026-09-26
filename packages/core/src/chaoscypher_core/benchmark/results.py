# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark result rows and their JSON file format.

Lives in Core so every producer of result rows - the CLI's local runner and
the MCP benchmark bridge - writes the same file the leaderboard reads.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from chaoscypher_core.benchmark.types import ScoreResult


if TYPE_CHECKING:
    from pathlib import Path


BENCHMARK_VERSION = "2.0"

_ALLOWED_TOP_LEVEL_KEYS = {"results", "schema_version"}
_RESULTS_SCHEMA_VERSION = 1


@dataclass
class BenchmarkResult:
    """One row in the benchmark output: results of running one model on one dataset.

    Versions, seed and temperature are pinned per row so heterogeneous runs
    are detectable downstream. A harness-track row (``pins_applied`` False,
    e.g. a model benchmarked through an MCP client) ran under settings the
    benchmark did not control: its ``temperature`` and ``thinking`` are None
    because nothing pinned or observed them, and ``harness`` names the client.
    ``seed`` is None when nothing the row measured used one (a harness-track
    grounded-chat row).
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
    seed: int | None
    temperature: float | None
    thinking: bool | None
    thinking_honoured: bool | None
    chunks_truncated: int
    chunks_aborted_by_loop: int
    extras: dict[str, Any] = field(default_factory=dict)
    pins_applied: bool = True
    """False when the benchmark could not pin seed/temperature/thinking - the
    model ran inside another harness (an MCP client) on its own settings."""
    harness: str | None = None
    """Who ran the model when the benchmark did not, e.g. ``"mcp:claude-code"``."""
    harness_settings: dict[str, Any] | None = None
    """What the harness reported about its own settings - effort, thinking
    mode, client version - since any of them can move the score. None when
    the benchmark ran the model itself or the harness reported nothing."""


HARNESS_SETTINGS_LEAD = ("effort", "thinking")
"""Harness settings shown first, in this order; the rest follow alphabetically."""


def format_harness_settings(settings: dict[str, Any] | None) -> list[str]:
    """Render harness settings as ``"key value"`` items in a stable order.

    ``effort`` and ``thinking`` lead (they move scores most), then the other
    keys alphabetically, so every surface words the same row the same way.
    """
    if not settings:
        return []
    lead = [k for k in HARNESS_SETTINGS_LEAD if k in settings]
    rest = sorted(k for k in settings if k not in HARNESS_SETTINGS_LEAD)
    return [f"{k} {_setting_text(settings[k])}" for k in (*lead, *rest)]


def _setting_text(value: Any) -> str:
    """Show a setting value: booleans as on/off, everything else as its string form."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _result_to_jsonable(r: BenchmarkResult) -> dict[str, Any]:
    """Convert a result to a JSON-safe dict (datetime to ISO string)."""
    d = asdict(r)
    d["timestamp"] = r.timestamp.isoformat()
    return d


def _result_from_jsonable(d: dict[str, Any]) -> BenchmarkResult:
    """Rebuild a BenchmarkResult from its JSON-safe dict form."""
    payload = dict(d)
    payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
    # Results written before 2026-09-22 have no thinking fields. Those runs did
    # execute with thinking off - it was the product default and the benchmark's
    # flag never reached the provider anyway - so `thinking=False` is accurate.
    # `thinking_honoured` stays None because nothing verified it at the time,
    # and "unverified" must not read as "confirmed".
    payload.setdefault("thinking", False)
    payload.setdefault("thinking_honoured", None)
    # Pre-2026-09-22 rows never recorded truncation; 0 means "not measured"
    # for those files, which the leaderboard states rather than implying clean.
    payload.setdefault("chunks_truncated", 0)
    payload.setdefault("chunks_aborted_by_loop", 0)
    payload.setdefault("extras", {})
    # Rows written before the MCP harness track existed all came from the
    # local runner, which applies the pins.
    payload.setdefault("pins_applied", True)
    payload.setdefault("harness", None)
    payload.setdefault("harness_settings", None)
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
    "BENCHMARK_VERSION",
    "HARNESS_SETTINGS_LEAD",
    "BenchmarkResult",
    "ScoreResult",
    "dump_results",
    "format_harness_settings",
    "load_results",
]
