# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark results file: harness-track fields and legacy compatibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from chaoscypher_core.benchmark.results import (
    BENCHMARK_VERSION,
    BenchmarkResult,
    dump_results,
    format_harness_settings,
    load_results,
)


if TYPE_CHECKING:
    from pathlib import Path


def _row(**kw: Any) -> BenchmarkResult:
    """A minimal valid row."""
    base: dict[str, Any] = {
        "model_id": "ollama/m",
        "model_label": "M",
        "dataset_id": "probes",
        "dataset_kind": "probes",
        "dataset_version": "0.1",
        "dataset_source": "builtin",
        "config_name": "probes",
        "headline_score": 50.0,
        "metrics": {},
        "latency_ms_total": 1,
        "latency_ms_per_chunk_p50": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": 0.0,
        "success": True,
        "error": None,
        "timestamp": datetime(2026, 9, 25, tzinfo=UTC),
        "benchmark_version": BENCHMARK_VERSION,
        "scorer_version": 1,
        "seed": 42,
        "temperature": 0.0,
        "thinking": False,
        "thinking_honoured": None,
        "chunks_truncated": 0,
        "chunks_aborted_by_loop": 0,
    }
    base.update(kw)
    return BenchmarkResult(**base)


def test_harness_track_row_round_trips(tmp_path: Path) -> None:
    """pins_applied, harness and unpinned temperature/thinking survive a dump/load."""
    path = tmp_path / "r.json"
    row = _row(
        pins_applied=False,
        harness="mcp:cursor",
        harness_settings={"effort": "high", "thinking": True, "budget": 8000},
        temperature=None,
        thinking=None,
    )
    dump_results([row], path)
    assert load_results(path) == [row]


def test_legacy_rows_load_as_pinned(tmp_path: Path) -> None:
    """Files written before the harness track load with pins applied, no harness."""
    path = tmp_path / "old.json"
    dump_results([_row()], path)
    payload = json.loads(path.read_text())
    for r in payload["results"]:
        del r["pins_applied"], r["harness"], r["harness_settings"]
    path.write_text(json.dumps(payload))
    (row,) = load_results(path)
    assert row.pins_applied is True
    assert row.harness is None
    assert row.harness_settings is None


def test_harness_settings_render_effort_and_thinking_first() -> None:
    """Effort and thinking lead, the rest follow A-Z; booleans read on/off."""
    assert format_harness_settings(None) == []
    assert format_harness_settings({"version": "2.3", "thinking": False, "effort": "high"}) == [
        "effort high",
        "thinking off",
        "version 2.3",
    ]
