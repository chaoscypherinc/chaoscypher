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
    monkeypatch.delenv("CI_TEST_SHARD", raising=False)
    assert _TOOL.main(["--root", str(tmp_path)]) == step
    assert [cmd[1:4] for cmd in calls] == [
        ["-m", "pytest", str(scripts.resolve())],
        ["-m", "pytest", str(tools.resolve())],
    ]
    assert calls[1][-2:] == ["-n", "auto"]  # the benchmark tools' suite runs in parallel


def _two_suites(tmp_path: Path) -> None:
    (tmp_path / "internal" / "scripts" / "tests").mkdir(parents=True)
    (tmp_path / "internal" / "benchmarks" / "tools" / "tests").mkdir(parents=True)


def test_each_suite_records_its_seconds_beside_the_shards_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sessions writing the shard's one record would leave the last suite's seconds only."""
    _two_suites(tmp_path)
    outs: list[str | None] = []

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        outs.append(kw["env"].get("CI_TEST_DURATIONS_OUT"))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(_TOOL.subprocess, "run", fake_run)
    monkeypatch.setenv(
        "CI_TEST_DURATIONS_OUT", str(Path("coverage-data") / "durations-shard-3.json")
    )
    assert _TOOL.main(["--root", str(tmp_path)]) == 0
    assert outs == [
        str(Path("coverage-data") / "durations-shard-3-scripts.json"),
        str(Path("coverage-data") / "durations-shard-3-benchmarks-tools.json"),
    ]
    monkeypatch.delenv("CI_TEST_DURATIONS_OUT")
    outs.clear()
    assert _TOOL.main(["--root", str(tmp_path)]) == 0
    assert outs == [None, None]  # no record asked for, none written


@pytest.mark.parametrize(("shard", "step"), [("2/10", 0), (None, 5)])
def test_an_empty_share_passes_only_in_a_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shard: str | None, step: int
) -> None:
    """A shard may get none of a small suite; unsharded, a suite that collects nothing is red."""
    _two_suites(tmp_path)
    monkeypatch.setattr(
        _TOOL.subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 5)
    )
    if shard:
        monkeypatch.setenv("CI_TEST_SHARD", shard)
    else:
        monkeypatch.delenv("CI_TEST_SHARD", raising=False)
    assert _TOOL.main(["--root", str(tmp_path)]) == step


def test_the_ci_step_and_make_target_both_run_it() -> None:
    """Its own step, which GitHub CI runs in every unit-test shard (each its share)."""
    root = _PATH.parents[1]
    run_ci = _load(root / "scripts" / "run_ci.py")
    assert run_ci._STEPS["internal-tool-tests"] == [
        ("uv run python scripts/run_internal_tool_tests.py", None)
    ]
    assert not any(
        "run_internal_tool_tests" in command for command, _ in run_ci._STEPS["lint-internal-refs"]
    )
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\ninternal-tool-tests:\n", 1)[1].split("\n\n", 1)[0]
    assert "uv run python scripts/run_internal_tool_tests.py" in recipe
    import yaml

    ci = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    shard_steps = [str(step.get("run", "")) for step in ci["jobs"]["python-tests"]["steps"]]
    assert any("run_ci.py --steps internal-tool-tests" in run for run in shard_steps)
    assert "CI_TEST_SHARD" in ci["jobs"]["python-tests"]["env"]


def _load(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
