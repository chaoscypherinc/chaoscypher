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
    "license-check",
    "test-cov-interface",
]


def test_docker_mode_matches_make_ci():
    """`--mode docker` mirrors `make ci`: shared checks + Docker tests + advisory diff + security."""
    assert _RUN_CI.build_plan("docker") == [
        *_COMMON,
        "docker-test",
        "coverage-diff-advisory",
        "security",
    ]


def test_local_mode_matches_make_ci_local():
    """`--mode local` mirrors `make ci-local`: shared checks + host tests + security."""
    assert _RUN_CI.build_plan("local") == [*_COMMON, "test-cov-internal", "security"]


def test_unknown_mode_raises():
    """An unknown mode is rejected rather than silently running a partial plan."""
    with pytest.raises(ValueError, match="unknown mode"):
        _RUN_CI.build_plan("bogus")


def test_every_planned_step_has_a_definition():
    """Each step in either plan has a command definition in _STEPS."""
    for mode in ("docker", "local"):
        for step in _RUN_CI.build_plan(mode):
            assert step in _RUN_CI._STEPS, f"{step} missing from _STEPS"
