# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the metrics-artifact gate (scripts/check_metrics_artifacts.py).

The gate used to resolve its target from ``__file__`` only, so a run from a
worktree of another branch silently checked the main checkout and printed the
same OK (2026-09-29). ``--root`` names the checkout, and every run prints the
absolute directory it checked. The metrics path is assembled from parts so the
internal-refs gate, which scans this tree, stays clean.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_gate():
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "check_metrics_artifacts.py"
    spec = importlib.util.spec_from_file_location("check_metrics_artifacts", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate()


def _metrics_dir(root: Path) -> Path:
    path = root / "internal" / "metrics"
    path.mkdir(parents=True)
    return path


def test_bad_artifact_fails_only_when_root_points_at_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad"
    (_metrics_dir(bad) / "scoreboard.yaml").write_bytes(
        b"a: 1\na: 2\nsession_usage: PLACEHOLDER_USAGE\n"
    )
    good = tmp_path / "good"
    (_metrics_dir(good) / "scoreboard.yaml").write_bytes(b"a: 1\n")

    assert _GATE.main(["--root", str(bad)]) == 1
    out = capsys.readouterr().out
    assert str((bad / "internal" / "metrics").resolve()) in out
    assert "duplicate key" in out and "PLACEHOLDER" in out

    assert _GATE.main(["--root", str(good)]) == 0
    out = capsys.readouterr().out
    assert "OK (1 artifact files checked in " in out
    assert str((good / "internal" / "metrics").resolve()) in out


def test_absent_tree_is_skipped_and_names_the_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _GATE.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "absent, skipped" in out
    assert str((tmp_path / "internal" / "metrics").resolve()) in out


def test_default_root_is_the_checkout_holding_the_script(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _GATE.main([])
    out = capsys.readouterr().out
    expected = (Path(_GATE.__file__).resolve().parent.parent / "internal" / "metrics").resolve()
    assert str(expected) in out


def test_trailing_blank_line_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (_metrics_dir(tmp_path) / "scoreboard.md").write_bytes(b"# board\n\n")
    assert _GATE.main(["--root", str(tmp_path)]) == 1
    assert "trailing blank line" in capsys.readouterr().out
