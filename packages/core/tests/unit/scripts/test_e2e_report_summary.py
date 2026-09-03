# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exit-code contract for ``scripts/e2e_report_summary.py``.

The script is the E2E workflow's aggregation gate: a report whose tests
*errored* (setup/teardown crash — pytest-json-report's ``summary.error``)
must fail the gate exactly like a failed test. Regression guard: errors
used to be printed but excluded from the exit code, so a suite that died
in setup exited green.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_script():
    """Import scripts/e2e_report_summary.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "e2e_report_summary.py"
    spec = importlib.util.spec_from_file_location("e2e_report_summary", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SCRIPT = _load_script()


def _write_report(directory: Path, name: str, summary: dict) -> None:
    payload = {"summary": summary, "duration": 1.0, "tests": []}
    (directory / f"{name}-report.json").write_text(json.dumps(payload))


def test_all_passed_exits_zero(tmp_path: Path) -> None:
    _write_report(tmp_path, "cli", {"passed": 3, "total": 3})
    assert _SCRIPT.main(str(tmp_path)) == 0


def test_failures_exit_nonzero(tmp_path: Path) -> None:
    _write_report(tmp_path, "cli", {"passed": 2, "failed": 1, "total": 3})
    assert _SCRIPT.main(str(tmp_path)) == 1


def test_errors_exit_nonzero(tmp_path: Path) -> None:
    """``summary.error`` with zero failures must still fail the gate."""
    _write_report(tmp_path, "cli", {"passed": 2, "error": 1, "total": 3})
    assert _SCRIPT.main(str(tmp_path)) == 1


def test_empty_directory_is_not_a_failure(tmp_path: Path) -> None:
    """No reports yet — informational, exit 0 (the Makefile relies on this)."""
    assert _SCRIPT.main(str(tmp_path)) == 0


def test_missing_directory_is_not_a_failure(tmp_path: Path) -> None:
    """A never-created reports dir is the same "nothing ran yet" case as an
    empty one — standalone `make e2e-report-summary` must not fail on it.
    """
    assert _SCRIPT.main(str(tmp_path / "does-not-exist")) == 0
