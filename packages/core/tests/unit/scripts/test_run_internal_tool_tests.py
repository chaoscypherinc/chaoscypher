# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""scripts/run_internal_tool_tests.py runs the tooling tests and no-ops on the public tree.

The subprocess is faked: a real nested pytest creates its own
``/tmp/pytest-of-<user>/pytest-N`` and prunes older ones, which deleted the
enclosing xdist run's basetemp mid-session (seen 2026-10-15).
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest


_PATH = Path(__file__).resolve().parents[5] / "scripts" / "run_internal_tool_tests.py"
_spec = importlib.util.spec_from_file_location("run_internal_tool_tests", _PATH)
assert _spec is not None
assert _spec.loader is not None
_TOOL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_TOOL)


def test_public_tree_is_a_no_op(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _TOOL.main(["--root", str(tmp_path)]) == 0
    assert "public tree" in capsys.readouterr().out


@pytest.mark.parametrize(("codes", "step"), [((0, 0), 0), ((1, 0), 1), ((0, 3), 3)])
def test_every_suite_runs_and_the_first_failure_is_the_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codes: tuple[int, int], step: int
) -> None:
    scripts = tmp_path / "internal" / "scripts" / "tests"
    tools = tmp_path / "internal" / "benchmarks" / "tools" / "tests"
    scripts.mkdir(parents=True)
    tools.mkdir(parents=True)
    calls: list[list[str]] = []
    results = iter(codes)

    def fake_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, next(results))

    monkeypatch.setattr(_TOOL.subprocess, "run", fake_run)
    assert _TOOL.main(["--root", str(tmp_path)]) == step
    assert [cmd[1:4] for cmd in calls] == [
        ["-m", "pytest", str(scripts.resolve())],
        ["-m", "pytest", str(tools.resolve())],
    ]
    assert calls[1][-2:] == ["-n", "auto"]  # the benchmark tools' suite runs in parallel


def test_the_ci_step_and_make_target_both_run_it() -> None:
    root = _PATH.parents[1]
    run_ci = (root / "scripts" / "run_ci.py").read_text(encoding="utf-8")
    assert "scripts/run_internal_tool_tests.py" in run_ci
    assert "scripts/run_internal_tool_tests.py" in (root / "Makefile").read_text(encoding="utf-8")
