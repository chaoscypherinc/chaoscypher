# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the portable CI runner's step-plan composition (scripts/run_ci.py).

The runner is the make-free equivalent of ``make ci`` / ``make ci-local``; these
tests pin the step ordering so the two stay in sync.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_run_ci():
    """Import scripts/run_ci.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "run_ci.py"
    spec = importlib.util.spec_from_file_location("run_ci", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RUN_CI = _load_run_ci()

# The host-side checks both modes share, in order (mirrors the Makefile prefix).
_COMMON = [
    "lint",
    "types",
    "typecheck",
    "lint-claude",
    "lint-secrets",
    "lint-internal-refs",
    "docstrings",
    "deadcode",
    "bundle-size",
    "docs-build",
    "license-check",
    "test-cov-interface",
]


def test_docker_mode_matches_make_ci():
    """`--mode docker` mirrors `make ci`: shared checks + Docker tests + advisory diff + e2e-cli + security."""
    assert _RUN_CI.build_plan("docker") == [
        *_COMMON,
        "docker-test",
        "coverage-diff-advisory",
        "e2e-cli",
        "security",
    ]


def test_local_mode_matches_make_ci_local():
    """`--mode local` mirrors `make ci-local`: shared checks + host tests + e2e-cli + security."""
    assert _RUN_CI.build_plan("local") == [*_COMMON, "test-cov-internal", "e2e-cli", "security"]


def test_security_step_runs_every_subcommand_and_reports_the_union(monkeypatch, capsys):
    """``security`` must not fail-fast (issue #519): all four gates run, the rollup names each, and the exit code is the first failure's."""
    expected = [command for command, _ in _RUN_CI._STEPS["security"]]
    codes = iter([3, 0, 1, 0])
    calls: list[str] = []

    def fake_shell(command: str, subdir: str | None) -> int:
        calls.append(command)
        return next(codes)

    monkeypatch.setattr(_RUN_CI, "_run_shell", fake_shell)
    assert _RUN_CI._run_step("security") == 3
    assert calls == expected, "every security sub-command must run, in plan order"
    out = capsys.readouterr().out
    assert out.count("PASS") == 2
    assert out.count("FAIL (exit 3)") == 1
    assert out.count("FAIL (exit 1)") == 1


def test_ordinary_steps_still_fail_fast(monkeypatch):
    """Only the aggregating steps run past a failure; everything else stops at the first red."""
    calls: list[str] = []

    def fake_shell(command: str, subdir: str | None) -> int:
        calls.append(command)
        return 1

    monkeypatch.setattr(_RUN_CI, "_run_shell", fake_shell)
    assert len(_RUN_CI._STEPS["lint"]) > 1
    assert _RUN_CI._run_step("lint") == 1
    assert len(calls) == 1


def test_unknown_mode_raises():
    """An unknown mode is rejected rather than silently running a partial plan."""
    with pytest.raises(ValueError, match="unknown mode"):
        _RUN_CI.build_plan("bogus")


def test_every_planned_step_has_a_definition():
    """Each step in either plan has a command definition in _STEPS."""
    for mode in ("docker", "local"):
        for step in _RUN_CI.build_plan(mode):
            assert step in _RUN_CI._STEPS, f"{step} missing from _STEPS"


def test_docker_image_build_is_defined_but_off_plan():
    """``docker-image-build`` must stay out of both default plans.

    It builds the production image and therefore needs a Docker daemon, which
    not every CI environment provides — putting it in a plan would fail those
    runs at this step. It is meant to be invoked explicitly
    (``run_ci.py --steps docker-image-build``) before cutting a release and on
    any change to ``packages/docker/Dockerfile``, which no default plan builds.
    """
    assert "docker-image-build" in _RUN_CI._STEPS
    for mode in ("docker", "local"):
        assert "docker-image-build" not in _RUN_CI.build_plan(mode)
