# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Benchmark results file: harness-track fields and legacy compatibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.benchmark.results import (
    BENCHMARK_VERSION,
    BenchmarkResult,
    current_app_version,
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
        del r["pins_applied"], r["harness"], r["harness_settings"], r["app_version"]
    path.write_text(json.dumps(payload))
    (row,) = load_results(path)
    assert row.pins_applied is True
    assert row.harness is None
    assert row.harness_settings is None
    # An unrecorded build stays unknown, not stamped with the reader's build.
    assert row.app_version is None


def test_new_rows_record_the_app_build(tmp_path: Path) -> None:
    """A row is stamped with the build that made it, and the stamp round-trips."""
    row = _row()
    assert row.app_version == current_app_version()
    path = tmp_path / "r.json"
    dump_results([row], path)
    assert load_results(path)[0].app_version == row.app_version


@pytest.mark.parametrize(("status", "suffix"), [("", ""), (" M results.py", ".dirty")])
def test_app_version_in_a_checkout_names_the_commit(
    monkeypatch: Any, status: str, suffix: str
) -> None:
    """From a source checkout the stamp is version+g<sha>, marked .dirty when edited."""
    import shutil
    import subprocess

    from chaoscypher_core import __version__

    def fake_git(argv: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        out = "abc123def\n" if "rev-parse" in argv else status
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", fake_git)
    current_app_version.cache_clear()
    try:
        assert current_app_version() == f"{__version__}+gabc123def{suffix}"
    finally:
        current_app_version.cache_clear()


def test_app_version_falls_back_to_package_version_without_git(monkeypatch: Any) -> None:
    """An installed wheel (no git, or not a checkout) records the package version alone."""
    import subprocess

    from chaoscypher_core import __version__

    def no_git(*_a: Any, **_k: Any) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    current_app_version.cache_clear()
    try:
        assert current_app_version() == __version__
    finally:
        current_app_version.cache_clear()


def test_harness_settings_render_effort_and_thinking_first() -> None:
    """Effort and thinking lead, the rest follow A-Z; booleans read on/off."""
    assert format_harness_settings(None) == []
    assert format_harness_settings({"version": "2.3", "thinking": False, "effort": "high"}) == [
        "effort high",
        "thinking off",
        "version 2.3",
    ]
